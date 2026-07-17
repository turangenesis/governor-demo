"""Step 4: the Governor — the judgment layer. PLAIN PYTHON, NO LLM.

Given a ProposedAction (an agent wants to send this outreach), the Governor scores
risk signals and decides: AUTO_SEND / ESCALATE / HOLD.

Two ideas make this more than a firewall:
  1. It *decides* which actions deserve a human's scarce attention (not a fixed rule).
  2. Load-shedding: human oversight is a BOUNDED resource. When the human's queue is
     saturated, low/medium-risk marginal cases are HELD so the human's attention is
     reserved for the cases that genuinely need judgment. (This is the paper's thesis.)

No LLM in here => deterministic => won't flake on stage => and *evaluable* (Step 5).

Run:  ./.venv/bin/python governor.py   # prints decisions on a few hand cases
"""
from __future__ import annotations

import re

from models import Candidate, HiringBrief, ProposedAction, Decision, GovernorDecision


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def _is_competitor(company: str, competitors) -> bool:
    """Robust competitor match: real GitHub companies are messy ('@Anthropic', 'anthropics')."""
    cn = _norm(company)
    return bool(cn) and any(_norm(k) and _norm(k) in cn for k in competitors)

# --- Tunable policy (the knobs an eval researcher would sweep) ---
# Tuned against eval_set.py to the correct ASYMMETRY: never miss a real risk
# (recall 1.0 / zero dangerous auto-sends), accepting a little over-escalation.
ESCALATE_THRESHOLD = 0.50   # risk >= this => a human must look
AUTO_THRESHOLD = 0.25       # risk <= this => safe to auto-send
MAX_HUMAN_QUEUE = 5         # human is saturated beyond this many pending escalations
HARD_ESCALATE = 0.80        # above this, ALWAYS escalate — never load-shed away a real risk

# Signal weights (contribution to aggregate risk, 0..1 before clipping).
# Weights are set so any single "must-see" signal (competitor, exec, sensitive, pushy)
# clears ESCALATE_THRESHOLD on its own.
W = {
    "competitor_poach": 0.60,   # emailing someone at a direct competitor is sensitive
    "exec_seniority": 0.60,     # exec-level outreach is high-stakes -> escalate alone
    "senior_seniority": 0.25,
    "low_draft_confidence": 0.40,
    "agent_concern": 0.25,      # per concern the agent itself surfaced (capped)
    "poor_match": 0.45,         # low profile match => embarrassing to send
    "sensitive_content": 0.60,  # salary/personal/legal terms -> escalate alone
    "pushy_content": 0.55,      # pushy/spammy phrasing -> escalate alone
    "too_short": 0.15,          # thin draft: mild signal only
}

SENSITIVE_TERMS = ("salary", "compensation", "visa", "sponsorship", "fired", "laid off", "medical")
PUSHY_TERMS = ("act now", "urgent", "last chance", "limited time", "asap")


def score_signals(action: ProposedAction, brief: HiringBrief) -> dict[str, float]:
    """Return the named risk signals that fired, mapped to their weight. Pure function."""
    c = action.candidate
    body = (action.body or "").lower()
    signals: dict[str, float] = {}

    if _is_competitor(c.current_company, brief.competitors):
        signals["competitor_poach"] = W["competitor_poach"]
    if c.seniority == "exec":
        signals["exec_seniority"] = W["exec_seniority"]
    elif c.seniority == "senior":
        signals["senior_seniority"] = W["senior_seniority"]
    if action.draft_confidence < 0.6:
        signals["low_draft_confidence"] = W["low_draft_confidence"] * (0.6 - action.draft_confidence) / 0.6
    if action.concerns:
        signals["agent_concern"] = min(0.4, W["agent_concern"] * len(action.concerns))
    if c.match_confidence < 0.6:
        signals["poor_match"] = W["poor_match"] * (0.6 - c.match_confidence) / 0.6
    if any(t in body for t in SENSITIVE_TERMS):
        signals["sensitive_content"] = W["sensitive_content"]
    if any(t in body for t in PUSHY_TERMS):
        signals["pushy_content"] = W["pushy_content"]
    if len(action.body or "") < 60:
        signals["too_short"] = W["too_short"]

    return signals


def risk_of(action: ProposedAction, brief: HiringBrief) -> tuple[float, dict[str, float]]:
    signals = score_signals(action, brief)
    score = min(1.0, sum(signals.values()))
    return score, signals


def govern(action: ProposedAction, brief: HiringBrief, human_queue_depth: int = 0) -> GovernorDecision:
    """The judgment call. GOVERNOR mode only (FIFO bypasses this and escalates everything)."""
    score, signals = risk_of(action, brief)
    reasons = [f"{k} (+{v:.2f})" for k, v in sorted(signals.items(), key=lambda kv: -kv[1])]

    # Hard escalate: a genuinely risky action is never load-shed away.
    if score >= HARD_ESCALATE:
        return GovernorDecision(Decision.ESCALATE, score, ["HARD-ESCALATE"] + reasons, signals)

    if score <= AUTO_THRESHOLD:
        return GovernorDecision(Decision.AUTO_SEND, score, reasons or ["clearly low risk"], signals)

    if score >= ESCALATE_THRESHOLD:
        # Needs a human — but if the human is saturated, park it instead of piling on.
        if human_queue_depth > MAX_HUMAN_QUEUE:
            return GovernorDecision(Decision.HOLD, score,
                                    [f"load-shed: human queue {human_queue_depth} > {MAX_HUMAN_QUEUE}"] + reasons,
                                    signals)
        return GovernorDecision(Decision.ESCALATE, score, reasons, signals)

    # Middle band (AUTO_THRESHOLD < score < ESCALATE_THRESHOLD): lean safe, auto-send.
    return GovernorDecision(Decision.AUTO_SEND, score, ["mid-band, below escalate threshold"] + reasons, signals)


if __name__ == "__main__":
    brief = HiringBrief(
        role="Senior Backend Engineer", must_haves="Python, distributed systems",
        hiring_company="Acme AI", competitors=("OpenAI", "Anthropic"),
    )
    cases = [
        ("clean auto-send", Candidate("A", "Stripe", "Staff Eng", "senior", "a@x.com", "SBE", 0.88),
         0.9, [], 0),
        ("competitor poach", Candidate("B", "Anthropic", "Staff Eng", "senior", "b@x.com", "SBE", 0.9),
         0.9, [], 0),
        ("low confidence + poor match", Candidate("C", "Wix", "Junior Dev", "junior", "c@x.com", "SBE", 0.35),
         0.4, ["unsure about fit"], 0),
        ("exec + saturated human", Candidate("D", "Acme", "VP Eng", "exec", "d@x.com", "SBE", 0.7),
         0.8, [], 8),
    ]
    print("=== Governor decisions (no LLM) ===\n")
    for label, cand, conf, concerns, qdepth in cases:
        a = ProposedAction("agent-x", cand, "Subject", "Hi, we're hiring, quick chat?", conf, concerns)
        d = govern(a, brief, human_queue_depth=qdepth)
        print(f"[{label}] queue={qdepth}")
        print(f"  -> {d.decision.value.upper()}  risk={d.risk_score:.2f}")
        print(f"     {', '.join(d.reasons)}\n")
