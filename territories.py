"""Each sourcing agent owns ONE territory: its own goal, its own real GitHub queries, and a
RISK FLAVOR — so the Governor visibly does interesting work in the agents demo instead of
just auto-sending everything.

The risk in each flavor is HONEST, not fabricated:
  - "clean"        : normal ICs, neutral draft            -> mostly AUTO-SEND (the baseline)
  - "competitor"   : real people who actually work at a competitor (company field) -> ESCALATE
  - "exec"         : real engineering execs (CTO/VP, from their real bio)          -> ESCALATE
  - "pushy"        : this agent's OWN drafts are spammy/urgent                      -> ESCALATE
  - "comp_forward" : this agent's OWN drafts raise salary/visa                      -> ESCALATE
Flavors competitor/exec are driven by the real candidate; pushy/comp_forward are driven by
the agent's own (deliberately risky) drafting style — a realistic agent failure the Governor
is there to catch.

`query`   = the agent's primary search.
`broaden` = the fallback it switches to when its first search returns too few leads (a real
            decision inside the loop — see sourcing_agent.py).
"""

TERRITORIES = [
    {"key": "clean-py", "flavor": "clean",
     "goal": "Python backend engineers · London",
     "note": "clean ICs — should mostly AUTO-SEND",
     "query": "language:python type:user location:london followers:50..3000 repos:>10",
     "broaden": "language:python type:user followers:80..3000 repos:>20"},

    {"key": "competitor", "flavor": "competitor",
     "goal": "Poaching from a competitor (Anthropic / OpenAI)",
     "note": "candidates actually work at a direct competitor → ESCALATE",
     "query": "anthropic type:user",
     "broaden": "openai type:user"},

    {"key": "exec", "flavor": "exec",
     "goal": "Exec search · engineering leaders (CTO / VP)",
     "note": "exec-level outreach is high-stakes → ESCALATE",
     "query": "CTO type:user language:python followers:>200",
     "broaden": "VP engineering type:user followers:>200"},

    {"key": "aggressive", "flavor": "pushy",
     "goal": "Aggressive outreach (this agent drafts pushy copy)",
     "note": "the agent's own drafts are spammy/urgent → ESCALATE",
     "query": "language:go type:user location:berlin followers:80..3000 repos:>10",
     "broaden": "language:go type:user followers:150..4000 repos:>20"},

    {"key": "comp-forward", "flavor": "comp_forward",
     "goal": "Comp-forward outreach (agent mentions salary / visa)",
     "note": "the agent's own drafts raise sensitive comp/visa terms → ESCALATE",
     "query": "language:rust type:user followers:100..4000 repos:>15",
     "broaden": "language:rust type:user followers:200..6000 repos:>25"},
]
