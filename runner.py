"""Step 2: the Orchestrator — 5 sourcing agents in background threads.

Pattern (the key de-risk): agents run in threads and push ProposedActions into a
thread-safe queue.Queue. A single Governor thread consumes the queue and routes each
proposal (auto-send / escalate / hold). The UI NEVER drives the graph — it only reads
snapshot() under a lock. This avoids the LangGraph-interrupt-inside-Streamlit-rerun trap.

Two modes:
  - "fifo"     : every proposal goes to the human queue (no Governor judgment).
  - "governor" : the Governor decides; auto-sends fire via Zero, risky ones escalate,
                 and once the human is saturated, marginal ones are load-shed (HELD).

Drives from the 40 labeled cases so the live demo matches the eval scoreboard exactly.
Set use_llm=True to have each agent re-draft via Bedrock (needs AWS creds).

Headless check:  ./.venv/bin/python runner.py
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from eval_set import build_cases, BRIEF
from governor import govern, MAX_HUMAN_QUEUE
from models import Decision, ProposedAction
from zero_client import send_outreach


@dataclass
class Item:
    action: ProposedAction
    decision: Optional[str] = None       # "auto_send" | "escalate" | "hold"
    risk: float = 0.0
    reasons: list = field(default_factory=list)
    ground_truth: str = ""               # for showing correctness live
    status: str = "pending"              # pending | sent | approved | denied | held
    result: dict = field(default_factory=dict)


class Orchestrator:
    def __init__(self, mode: str = "governor", zero_mode: str = "stub",
                 n_agents: int = 5, step_delay: float = 0.25, use_llm: bool = False,
                 source: str = "labeled"):
        self.mode = mode
        self.zero_mode = zero_mode
        self.n_agents = n_agents
        self.step_delay = step_delay
        self.use_llm = use_llm
        self.source = source          # "labeled" (scoreboard set) | "discovered" (live GitHub)

        self._lock = threading.Lock()
        self._inbox: "queue.Queue[tuple[str, ProposedAction, str]]" = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._producers_done = threading.Event()   # set when all agent threads finish
        self._active_producers = 0
        self._seq_counter = 0                       # unique arrival seq across agents

        # shared state (guard with self._lock)
        self.agent_status: dict[str, str] = {f"agent-{i+1}": "idle" for i in range(n_agents)}
        self.human_queue: list[Item] = []     # escalated, awaiting human
        self.sent: list[Item] = []            # auto-sent or human-approved
        self.denied: list[Item] = []
        self.held: list[Item] = []
        self.events: list[str] = []
        self.processed = 0
        self.total = 0
        self._llm = None

    # --- lifecycle ---
    def start(self):
        gov = threading.Thread(target=self._governor_worker, daemon=True)
        gov.start()
        self._threads.append(gov)

        if self.source == "agents":
            # Level 2: each agent autonomously sources its OWN territory.
            from territories import TERRITORIES
            terrs = TERRITORIES[:self.n_agents]
            self.n_agents = len(terrs)
            with self._lock:
                self.agent_status = {f"agent-{i+1}": "idle" for i in range(self.n_agents)}
                self._active_producers = self.n_agents
            for i, terr in enumerate(terrs):
                t = threading.Thread(target=self._sourcing_agent_worker,
                                     args=(f"agent-{i+1}", terr), daemon=True)
                t.start()
                self._threads.append(t)
            return

        # labeled / discovered pipeline: deal pre-built cases round-robin to n_agents
        if self.source == "discovered":
            from discover import discovered_cases
            cases = discovered_cases()
        else:
            cases = build_cases()
        self.total = len(cases)
        buckets: list[list] = [[] for _ in range(self.n_agents)]
        for i, c in enumerate(cases):
            buckets[i % self.n_agents].append(c)
        with self._lock:
            self._active_producers = self.n_agents
        for i in range(self.n_agents):
            t = threading.Thread(target=self._agent_worker, args=(f"agent-{i+1}", buckets[i]), daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()

    def _next_seq(self) -> int:
        with self._lock:
            self._seq_counter += 1
            return self._seq_counter

    def _producer_finished(self):
        with self._lock:
            self._active_producers -= 1
            done = self._active_producers <= 0
        if done:
            self._producers_done.set()

    def _log(self, msg: str):
        with self._lock:
            self.events.append(msg)

    def _get_llm(self):
        if self._llm is None:
            from agent import make_llm
            self._llm = make_llm()
        return self._llm

    # --- workers ---
    def _agent_worker(self, agent_id: str, cases: list):
        try:
            for case in cases:
                if self._stop.is_set():
                    return
                with self._lock:
                    self.agent_status[agent_id] = f"drafting → {case.action.candidate.name}"
                action = case.action
                action.agent_id = agent_id
                if self.use_llm:
                    try:
                        from agent import run_once
                        action = run_once(case.action.candidate, BRIEF, self._get_llm(), agent_id, case.action.seq)
                    except Exception as e:
                        self._log(f"{agent_id} LLM draft failed ({str(e)[:60]}), using prewritten draft")
                time.sleep(self.step_delay)
                with self._lock:
                    self.agent_status[agent_id] = f"proposed → {case.action.candidate.name}"
                self._inbox.put((agent_id, action, case.ground_truth))
            with self._lock:
                self.agent_status[agent_id] = "done"
        finally:
            self._producer_finished()

    def _sourcing_agent_worker(self, agent_id: str, territory: dict):
        """Level 2: this agent runs its OWN search→draft→critique→propose loop for its territory."""
        from sourcing_agent import run_sourcing_agent

        try:
            with self._lock:
                self.agent_status[agent_id] = f"sourcing → {territory['goal']}"

            def on_proposal(pa: ProposedAction):
                if self._stop.is_set():
                    return
                pa.agent_id = agent_id
                pa.seq = self._next_seq()
                with self._lock:
                    self.total += 1
                    self.agent_status[agent_id] = f"proposed → {pa.candidate.name}"
                self._inbox.put((agent_id, pa, ""))   # live leads have no ground-truth label
                time.sleep(self.step_delay)

            llm = self._get_llm() if self.use_llm else None
            try:
                run_sourcing_agent(territory, llm=llm, use_llm=self.use_llm, on_proposal=on_proposal)
            except Exception as e:
                self._log(f"{agent_id} sourcing failed ({str(e)[:70]})")
            with self._lock:
                self.agent_status[agent_id] = "done"
        finally:
            self._producer_finished()

    def _governor_worker(self):
        seen = 0
        while not self._stop.is_set():
            try:
                agent_id, action, gt = self._inbox.get(timeout=0.2)
            except queue.Empty:
                # all producers finished AND nothing left to consume → done
                if self._producers_done.is_set() and self._inbox.empty():
                    return
                continue

            if self.mode == "fifo":
                item = Item(action, "escalate", 0.0, ["FIFO: no judgment"], gt, "pending")
                with self._lock:
                    self.human_queue.append(item)
                    self.processed += 1
                continue

            # governor mode
            with self._lock:
                depth = len(self.human_queue)
            gd = govern(action, BRIEF, human_queue_depth=depth)
            item = Item(action, gd.decision.value, gd.risk_score, gd.reasons, gt)

            if gd.decision == Decision.AUTO_SEND:
                res = send_outreach(action, mode=self.zero_mode)
                item.status, item.result = "sent", res
                with self._lock:
                    self.sent.append(item)
                    self.processed += 1
            elif gd.decision == Decision.ESCALATE:
                item.status = "pending"
                with self._lock:
                    self.human_queue.append(item)
                    self.processed += 1
            else:  # HOLD (load-shed)
                item.status = "held"
                with self._lock:
                    self.held.append(item)
                    self.processed += 1
            seen += 1

    # --- human actions (called by the UI) ---
    def _pop_from_queue(self, seq: int) -> Optional[Item]:
        with self._lock:
            for i, item in enumerate(self.human_queue):
                if item.action.seq == seq:
                    return self.human_queue.pop(i)
        return None

    def _promote_held(self):
        """Human freed capacity → surface the highest-risk deferred case for review."""
        with self._lock:
            if not self.held or len(self.human_queue) >= MAX_HUMAN_QUEUE:
                return
            self.held.sort(key=lambda it: -it.risk)
            it = self.held.pop(0)
            it.status = "pending"
            it.decision = "escalate"
            it.reasons = ["promoted from hold (human freed capacity)"] + it.reasons
            self.human_queue.append(it)

    def approve(self, seq: int):
        it = self._pop_from_queue(seq)
        if it is None:
            return
        res = send_outreach(it.action, mode=self.zero_mode)
        it.status, it.result = "approved", res
        with self._lock:
            self.sent.append(it)
        self._promote_held()

    def deny(self, seq: int):
        it = self._pop_from_queue(seq)
        if it is None:
            return
        it.status = "denied"
        with self._lock:
            self.denied.append(it)
        self._promote_held()

    # --- snapshot for UI ---
    def snapshot(self) -> dict:
        with self._lock:
            n_auto = len([i for i in self.sent if i.decision == "auto_send"])
            n_human_sent = len([i for i in self.sent if i.decision != "auto_send"])
            human_facing = len(self.human_queue) + n_human_sent + len(self.denied)
            autonomy = 100.0 * n_auto / self.processed if self.processed else 0.0
            # dangerous auto-sends = auto-sent items whose ground truth said escalate
            dangerous = len([i for i in self.sent
                             if i.decision == "auto_send" and i.ground_truth == "escalate"])
            return {
                "mode": self.mode,
                "agent_status": dict(self.agent_status),
                "queue_depth": len(self.human_queue),
                "human_queue": list(self.human_queue),
                "sent": list(self.sent),
                "denied": list(self.denied),
                "held": list(self.held),
                "processed": self.processed,
                "total": self.total,
                "auto_sent": n_auto,
                "human_facing": human_facing,
                "autonomy_pct": autonomy,
                "dangerous_auto_sends": dangerous,
                "events": list(self.events[-12:]),
            }


def run_headless(mode: str, source: str = "labeled") -> dict:
    orc = Orchestrator(mode=mode, zero_mode="stub", step_delay=0.02, source=source)
    orc.start()
    # wait for completion: all producers done AND queue drained
    for _ in range(4000):
        if orc._producers_done.is_set() and orc._inbox.empty() and \
                orc.processed >= orc.total and all(
                    s in ("done", "idle") for s in orc.agent_status.values()):
            break
        time.sleep(0.02)
    orc.stop()
    return orc.snapshot()


if __name__ == "__main__":
    for mode in ("fifo", "governor"):
        snap = run_headless(mode)
        print(f"=== {mode.upper()} ===")
        print(f"  processed {snap['processed']}/{snap['total']}")
        print(f"  human-facing (queue+approved+denied): {snap['human_facing']}")
        print(f"  auto-sent: {snap['auto_sent']} | held (load-shed): {len(snap['held'])}")
        print(f"  autonomy: {snap['autonomy_pct']:.0f}% | queue depth now: {snap['queue_depth']}")
        print(f"  DANGEROUS auto-sends: {snap['dangerous_auto_sends']}")
        print()
