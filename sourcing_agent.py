"""Level 2: an AUTONOMOUS sourcing agent that owns ONE territory and runs a real loop.

This is the difference between "5 threads we called agents" and 5 genuine agents. Each
agent has its own goal (a territory), its own tools (search / draft / critique), and it
DECIDES its own next step based on what it observes:

    START → search its territory
              │
              ├─ too few leads & tries left?  ──► broaden query, search again  (DECISION 1)
              │
              └─ enough leads ──► for each lead:
                                    draft  →  self-critique
                                      └─ draft weak? ──► revise once           (DECISION 2)
                                    → propose to the Governor
                                  → END

Built as a LangGraph StateGraph: DECISION 1 is a conditional edge (a real loop back to
`search`), not a straight line. That conditional edge is the "loop engineering".

Works with or without an LLM:
  - use_llm=True : Claude on Bedrock drafts/revises (agent.run_once).
  - use_llm=False: deterministic template draft + heuristic critique — stage-safe, offline.
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from discover import search_by_query
from eval_set import BRIEF, _CLEAN_BODY
from models import ProposedAction

MIN_LEADS = 2          # below this, the agent broadens and searches again
MAX_SEARCHES = 2       # cap on search attempts per territory
PER_TERRITORY = 4      # candidates each agent proposes


class SourcingState(TypedDict, total=False):
    territory: dict
    attempts: int
    leads: list          # list[Candidate]
    proposals: list      # list[ProposedAction]
    log: list


def _draft(cand, llm, use_llm, flavor="clean", revise_hint=None):
    """Draft an outreach email. The territory's flavor shapes the draft.

    pushy / comp_forward drafts are deterministic (so the risky content is guaranteed for the
    demo). Neutral flavors (clean/competitor/exec) use the LLM when enabled — their risk, if
    any, comes from the real candidate (competitor company, exec seniority), not the copy.
    """
    first = cand.name.split()[0]
    if flavor == "pushy":
        return ("ACT NOW — last chance to join Acme AI",
                f"{first}, this is URGENT — reply ASAP. This is your LAST CHANCE to join Acme AI "
                f"for the {BRIEF.role} role. Act now — limited time only!",
                cand.match_confidence, [])
    if flavor == "comp_forward":
        return ("We can beat your salary + sort visa",
                f"Hi {first}, we can beat your current salary and sort visa sponsorship fast for "
                f"the {BRIEF.role} role at Acme AI. Heard your team had layoffs — let's talk.",
                cand.match_confidence, [])

    if use_llm and llm is not None and not revise_hint:
        try:
            from agent import run_once
            pa = run_once(cand, BRIEF, llm)
            return pa.subject, pa.body, pa.draft_confidence, list(pa.concerns)
        except Exception:
            pass  # fall through to template on any Bedrock error
    if revise_hint:
        body = (f"Hi {first}, I came across your {cand.current_title} work and was genuinely "
                f"impressed. We're building reliability infrastructure at {BRIEF.hiring_company}; "
                f"your background looks like a strong fit for the {BRIEF.role} role. "
                f"Open to a short call next week?")
    else:
        body = _CLEAN_BODY.format(name=first, company=cand.current_company, role=BRIEF.role)
    return "A role you might like", body, cand.match_confidence, []


def _critique(cand, body) -> tuple[bool, str]:
    """Self-check: is this draft actually specific enough to send? (a real, data-driven test)"""
    if cand.current_company.startswith("Independent"):
        return True, "not specific — candidate lists no company"
    if len(body) < 60:
        return True, "draft too short"
    return False, ""


def build_sourcing_agent(llm=None, use_llm=False, on_proposal=None,
                         search_fn=search_by_query):
    """Compile one agent's territory loop. `on_proposal(pa)` streams each proposal live."""

    def search_node(state: SourcingState) -> SourcingState:
        terr = state["territory"]
        attempts = state.get("attempts", 0)
        query = terr["query"] if attempts == 0 else terr["broaden"]
        found = search_fn(query, limit=PER_TERRITORY)
        have = {c.name for c in state.get("leads", [])}
        fresh = [c for c in found if c.name not in have]
        return {
            "attempts": attempts + 1,
            "leads": state.get("leads", []) + fresh,
            "log": state.get("log", []) + [f"search #{attempts + 1} → {len(fresh)} new leads"],
        }

    def decide_after_search(state: SourcingState) -> str:
        # DECISION 1: too few leads and tries left → broaden & search again; else draft.
        if len(state.get("leads", [])) < MIN_LEADS and state.get("attempts", 0) < MAX_SEARCHES:
            return "search"
        return "draft"

    def draft_node(state: SourcingState) -> SourcingState:
        flavor = state["territory"].get("flavor", "clean")
        proposals, log = [], list(state.get("log", []))
        for cand in state.get("leads", [])[:PER_TERRITORY]:
            subj, body, conf, concerns = _draft(cand, llm, use_llm, flavor=flavor)
            if flavor in ("clean", "competitor", "exec"):   # deliberately-risky flavors keep their copy
                weak, why = _critique(cand, body)
                if weak:                              # DECISION 2: revise a weak draft once
                    subj, body, conf, concerns = _draft(cand, llm, use_llm, flavor=flavor, revise_hint=why)
                    concerns = concerns + [f"revised after self-critique ({why})"]
                    log.append(f"revised draft for {cand.name}: {why}")
            pa = ProposedAction("", cand, subj, body, conf, concerns)
            proposals.append(pa)
            if on_proposal:
                on_proposal(pa)
        return {"proposals": proposals, "log": log + [f"proposed {len(proposals)}"]}

    g = StateGraph(SourcingState)
    g.add_node("search", search_node)
    g.add_node("draft", draft_node)
    g.add_edge(START, "search")
    g.add_conditional_edges("search", decide_after_search,
                            {"search": "search", "draft": "draft"})
    g.add_edge("draft", END)
    return g.compile()


def run_sourcing_agent(territory, llm=None, use_llm=False, on_proposal=None,
                       search_fn=search_by_query) -> dict:
    graph = build_sourcing_agent(llm, use_llm, on_proposal, search_fn)
    return graph.invoke({"territory": territory, "attempts": 0, "leads": [], "log": []})


if __name__ == "__main__":
    from territories import TERRITORIES

    for terr in TERRITORIES[:2]:
        print(f"\n=== agent territory: {terr['goal']} ===")
        out = run_sourcing_agent(terr, use_llm=False)
        for line in out["log"]:
            print("  ·", line)
        for pa in out["proposals"]:
            print(f"   → {pa.candidate.name} @ {pa.candidate.current_company} "
                  f"(conf {pa.draft_confidence:.2f})"
                  f"{'  [revised]' if any('revised' in c for c in pa.concerns) else ''}")
