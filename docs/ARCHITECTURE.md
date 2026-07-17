# 🛡️ The Governor — Architecture

How the system flows, end to end. One hiring brief in → real leads sourced → drafted →
**judged by the Governor** → sent / escalated / held → measured against ground truth.

---

## At a glance

```
                       ┌───────────────────────────┐
                       │   📋  HIRING BRIEF          │
                       │   role · must-haves ·      │
                       │   competitor list          │
                       └─────────────┬─────────────┘
                                     │  split into 5 territories
      ┌───────────┬───────────┬──────┴──────┬───────────┬───────────┐
      ▼           ▼           ▼             ▼           ▼
 ┌─────────┐ ┌─────────┐ ┌─────────┐  ┌─────────┐ ┌─────────┐
 │ agent-1 │ │ agent-2 │ │ agent-3 │  │ agent-4 │ │ agent-5 │   5 AUTONOMOUS AGENTS
 │ clean   │ │ compet. │ │ exec    │  │ pushy   │ │ comp-   │   each runs its own loop:
 │ ICs     │ │ poach   │ │ search  │  │ draft   │ │ forward │   search → (broaden?) →
 └────┬────┘ └────┬────┘ └────┬────┘  └────┬────┘ └────┬────┘   draft → (revise?) → propose
      └───────────┴───────────┼────────────┴───────────┘
                              ▼
                   ┌─────────────────────┐
                   │   📥  SHARED QUEUE    │   all proposals land here
                   └──────────┬──────────┘
                              ▼
             ╔═════════════════════════════════╗
             ║   🛡️   THE  GOVERNOR             ║   deterministic · NO LLM
             ║   score risk signals → decide   ║   evaluable · won't flake on stage
             ╚════════════════┬════════════════╝
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
    🟢 AUTO-SEND        🟠 ESCALATE         🔵 HOLD
    (low risk)          (human must see)    (human saturated → defer)
          │                  │                  │
          ▼                  ▼                  ▼
    ✉️  Zero.xyz        👤 Human            ↩ back to queue
       (stub)             approve / deny       when capacity frees
          
    ─────────────────────────────────────────────────────────────
    📊  EVAL SCOREBOARD   ·   40 labeled cases
        escalation recall 1.00   ·   0 dangerous auto-sends
```

**Runs fully offline:** candidate search replays a local cache, drafting is templated (no LLM),
sends are stubbed — **no credentials, no network, no env vars required.**

---

## 1. The whole system at a glance

```mermaid
flowchart TD
    BRIEF["📋 Hiring brief<br/>(role · must-haves · competitors)"]

    subgraph FLEET["🤖 Sourcing fleet — 5 autonomous agents (each owns a territory)"]
        A1["agent-1<br/>Clean Python ICs"]
        A2["agent-2<br/>Competitor poach"]
        A3["agent-3<br/>Exec search"]
        A4["agent-4<br/>Aggressive outreach"]
        A5["agent-5<br/>Comp-forward"]
    end

    GH["🌐 GitHub public API<br/>(real engineers · cached for replay)"]
    Q["📥 One shared queue<br/>(all proposals land here)"]

    GOV{"🛡️ THE GOVERNOR<br/>deterministic · no LLM<br/>scores risk signals"}

    SEND["🟢 AUTO-SEND<br/>(safe)"]
    ESC["🟠 ESCALATE<br/>(human must see)"]
    HOLD["🔵 HOLD<br/>(human saturated → defer)"]

    HUMAN["👤 Human recruiter<br/>Approve / Deny"]
    ZERO["✉️ Zero.xyz send<br/>(stub by default)"]
    EVAL["📊 Eval scoreboard<br/>40 labeled cases → recall, dangerous=0"]

    BRIEF --> FLEET
    FLEET -->|"search their territory"| GH
    GH -->|"real candidates"| FLEET
    FLEET -->|"ProposedAction (draft email)"| Q
    Q --> GOV
    GOV --> SEND --> ZERO
    GOV --> ESC --> HUMAN
    GOV --> HOLD -.->|"capacity frees → promote"| HUMAN
    HUMAN -->|"approve"| ZERO
    GOV -. "policy measured against" .- EVAL
```

**Read it as:** the agents *propose*, the Governor *decides*, the human sees *only what needs
judgment*, and the scoreboard *proves the decisions are correct*.

---

## 2. What one agent actually does (the loop)

Each of the 5 agents runs this independently, in its own thread, for its own territory.
The **diamonds are real decisions** — that's what makes it an agent loop, not a script.

```mermaid
flowchart TD
    START([territory: goal + query + risk flavor]) --> SEARCH["🔍 search GitHub<br/>for my territory"]
    SEARCH --> D1{"enough leads?"}
    D1 -->|"too few (&lt;2) & tries left"| BROADEN["broaden query"] --> SEARCH
    D1 -->|"enough"| DRAFT["✍️ draft outreach<br/>(template / flavor / LLM)"]
    DRAFT --> D2{"self-critique:<br/>draft specific enough?"}
    D2 -->|"weak"| REVISE["revise once"] --> PROPOSE
    D2 -->|"ok"| PROPOSE["📤 propose to Governor"]
    PROPOSE --> DONE([next lead / done])
```

- **Decision 1** (broaden & re-search) lives as a LangGraph *conditional edge* — `search → search`.
- **Decision 2** (revise a weak draft) fires when a lead has no company / the draft is too thin.
- The **risk flavor** shapes the draft/target so different agents trip different Governor signals.

---

## 3. How the Governor decides (deterministic)

```mermaid
flowchart TD
    PA["ProposedAction"] --> SIG["score risk signals:<br/>competitor_poach · exec_seniority<br/>sensitive_content · pushy_content<br/>poor_match · low_confidence …"]
    SIG --> SCORE["aggregate risk 0..1"]
    SCORE --> H{"risk ≥ 0.80?"}
    H -->|yes| ESC["🟠 ESCALATE<br/>(hard — never load-shed)"]
    H -->|no| L{"risk ≤ 0.25?"}
    L -->|yes| AUTO["🟢 AUTO-SEND"]
    L -->|no| M{"risk ≥ 0.50?"}
    M -->|"yes & human queue full"| HOLD["🔵 HOLD (load-shed)"]
    M -->|"yes & human free"| ESC2["🟠 ESCALATE"]
    M -->|no| AUTO2["🟢 AUTO-SEND (mid-band, lean safe)"]
```

The thresholds and signal weights are the knobs an eval researcher sweeps — all in `governor.py`.

---

## 4. Where everything lives

```mermaid
flowchart LR
    subgraph SRC["Source of truth per concern"]
        T["territories.py<br/>each agent's identity"]
        SA["sourcing_agent.py<br/>the agent loop (LangGraph)"]
        DC["discover.py<br/>GitHub search + cache"]
        AG["agent.py<br/>LLM draft prompt (DRAFT_SYSTEM)"]
        GO["governor.py<br/>the judgment policy"]
        ES["eval_set.py<br/>40 labeled cases"]
        EV["evaluate.py<br/>scoreboard / confusion matrix"]
        RU["runner.py<br/>5 threads + queue orchestration"]
        AP["app.py<br/>Streamlit UI"]
    end
```

| Concern | File |
|---|---|
| Each agent's **identity** (territory, query, risk flavor) | `territories.py` |
| The **agent loop** (search → broaden → draft → critique → propose) | `sourcing_agent.py` |
| The **search tool** (GitHub + per-query cache + rate-limit guard) | `discover.py` |
| The **LLM drafting prompt** (only used when Bedrock toggle is ON) | `agent.py` |
| The **Governor** (risk signals, thresholds, load-shedding) | `governor.py` |
| **Ground-truth** eval set + **scoreboard** | `eval_set.py`, `evaluate.py` |
| **Orchestration** (5 threads → 1 queue → Governor) | `runner.py` |
| **UI** | `app.py` |

---

## 5. Runtime & data — what a local run actually uses

```mermaid
flowchart TD
    RUN["▶ Start (source = agents)"] --> C{"cache present?"}
    C -->|"yes (default now)"| CACHE["replay cache_*.json<br/>❌ no network · no API · no token"]
    C -->|"no"| LIVE["GitHub public API<br/>free · no key · 60/hr · then cached"]
    RUN --> LLM{"LLM drafting toggle"}
    LLM -->|"OFF (default)"| TMPL["template / scripted drafts<br/>❌ no Bedrock · no subscription"]
    LLM -->|"ON"| BR["AWS Bedrock (needs AWS creds)"]
    RUN --> Z{"Zero.xyz mode"}
    Z -->|"stub (default)"| STUB["fake send · no network"]
    Z -->|"real"| ZR["Zero.xyz (needs key) · falls back to stub"]
```

**Bottom line for a run right now:** it uses **cached search data from the earlier real run** —
fully **offline**, **no API keys, no AWS/Bedrock, no Claude subscription, no tokens**. It's a
self-contained demo. The only ways to touch a paid/live service are flipping the LLM toggle
(Bedrock) or Zero mode to real — neither is needed.
</content>
