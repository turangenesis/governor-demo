# 🛡️ The Governor

**The oversight & eval layer for autonomous recruiting agents.**

Five Fillmore-style sourcing agents each want to send cold outreach. The **Governor** — a
plain-Python judgment layer (no LLM) — decides which sends fire autonomously and which a human
recruiter must see, reserving scarce human attention for the calls that need judgment.

Stopping an agent is trivial (an `if`). The hard, valuable problem is the **judgment layer**:
*which* of an agent's real-world actions deserve a bounded human's attention — and how do you
*measure* that the policy is right. That's this demo.

## The result

```
40 proposed sends →  FIFO:      human sees all 40   | autonomy 0%
                     GOVERNOR:  human sees ~7       | autonomy 62% | held (deferred) 8
  DANGEROUS auto-sends: 0   (recall 1.00 — never let a risky email through)
  false escalations:   2   (deliberate over-caution: a wasted glance << a bad send)
```

Under live load the Governor **degrades by deferring (HOLD), never by unsafely auto-sending** —
the 0-dangerous invariant holds even when the human is saturated.

## Sponsors

- **AWS Bedrock** — the LLM drafting each outreach (`ChatBedrockConverse`).
- **Zero.xyz** — the real-world action layer the Governor gates (the gated send).
- **Akash** — hosts the deployed app.

## Run locally

```bash
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in AWS + Zero (see docs/SETUP.md)

# component checks (no creds needed):
./.venv/bin/python agent.py --dry     # agent graph wiring
./.venv/bin/python governor.py        # judgment policy on hand cases
./.venv/bin/python evaluate.py        # the eval scoreboard
./.venv/bin/python runner.py          # FIFO vs Governor, headless

# the demo:
./.venv/bin/streamlit run app.py
```

In the UI: hit **Start** in **FIFO** first (human drowns in 40), then **Governor**
(judgment layer + load-shedding). Work the queue with Approve/Deny.

## Files

| File | Role |
|------|------|
| `models.py` | shared dataclasses (Candidate, ProposedAction, Decision…) |
| `agent.py` | one sourcing agent as a LangGraph graph on Bedrock (`--dry` = fake LLM) |
| `governor.py` | **the judgment layer** — plain Python, no LLM, tunable policy |
| `eval_set.py` | 40 hand-labeled ground-truth outreach actions |
| `evaluate.py` | runs the policy over the set → confusion matrix + metrics |
| `discover.py` | GitHub search tool — real candidate discovery, cached for offline replay |
| `territories.py` | each agent's territory: goal, search query, risk flavor |
| `sourcing_agent.py` | the autonomous agent loop (LangGraph): search → broaden → draft → critique → propose |
| `zero_client.py` | Zero.xyz send with demo-safe stub fallback |
| `runner.py` | orchestrator: 5 agents in threads → queue → Governor routing |
| `app.py` | single-file Streamlit UI |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full system flow and diagrams.

## Runs fully offline

The default demo needs **no credentials and no network** — candidate search replays from a
local cache, drafting uses templates (no LLM), and sends are stubbed. Live services (AWS
Bedrock drafting, real Zero.xyz sends) are strictly opt-in and never required.

## Deploy a public demo (Streamlit Community Cloud)

The app runs with zero secrets, so a public deploy is straightforward:

1. Push this repo to a **public GitHub repo** (secrets and private notes are already
   `.gitignore`d — see the file list below).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → point it at your
   repo and `app.py` → **Deploy**. You get a public URL in ~2 minutes.
3. In the deployed app, use the **Labeled set** source (synthetic candidates) — it tells the
   full governance story with no real individuals shown and needs no cache files.

No environment variables are required. To also enable live GitHub discovery on the deploy,
add a `GITHUB_TOKEN` in Streamlit **Secrets** (optional; the labeled demo doesn't need it).
