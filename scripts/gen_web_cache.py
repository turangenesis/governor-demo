"""Generate docs/demo_cache.json — the cached snapshot the static web demo loads instantly.

Runs the 5 territory agents over their cached GitHub results, scores each proposal with the
real Governor, and writes everything the page needs (candidates, drafts, decisions, reasons,
scoreboard) as JSON. Zero cost to run again; the page ships this so visitors pay nothing.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discover import search_by_query  # noqa: E402
from eval_set import BRIEF  # noqa: E402
from evaluate import evaluate  # noqa: E402
from governor import govern  # noqa: E402
from models import ProposedAction  # noqa: E402
from sourcing_agent import _draft  # noqa: E402
from territories import TERRITORIES  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "demo_cache.json")


def build() -> dict:
    agents = []
    for terr in TERRITORIES:
        cands = search_by_query(terr["query"], limit=4)  # served from cache; no network
        rows = []
        for c in cands:
            subj, body, conf, concerns = _draft(c, None, use_llm=False, flavor=terr["flavor"])
            pa = ProposedAction("agent", c, subj, body, conf, concerns, seq=0)
            gd = govern(pa, BRIEF)
            rows.append({
                "name": c.name, "company": c.current_company, "title": c.current_title,
                "seniority": c.seniority, "match": c.match_confidence,
                "subject": subj, "body": body,
                "decision": gd.decision.value, "risk": round(gd.risk_score, 2),
                "reasons": gd.reasons[:3],
            })
        agents.append({"key": terr["key"], "goal": terr["goal"], "flavor": terr["flavor"],
                       "note": terr["note"], "candidates": rows})

    sb = evaluate()
    return {
        "brief": {"role": BRIEF.role, "must_haves": BRIEF.must_haves,
                  "hiring_company": BRIEF.hiring_company, "competitors": list(BRIEF.competitors)},
        "agents": agents,
        "scoreboard": {
            "recall": round(sb.escalation_recall, 2),
            "precision": round(sb.escalation_precision, 2),
            "dangerous": sb.false_auto_send,
            "human_load_saved_pct": round(sb.human_load_saved_pct),
        },
    }


if __name__ == "__main__":
    data = build()
    with open(OUT, "w") as f:
        json.dump(data, f, indent=2)
    n = sum(len(a["candidates"]) for a in data["agents"])
    print(f"wrote {OUT}: {len(data['agents'])} agents, {n} candidates")
