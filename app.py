"""Step 6: single-file Streamlit UI for The Governor.

Left  = 5 Fillmore-style sourcing agents, live status.
Right = the shared approval queue a human works.
Top   = counters (autonomy %, human-facing, queue depth, DANGEROUS auto-sends).
Bottom= the eval scoreboard (static proof the judgment is correct).

The UI only polls Orchestrator.snapshot() — it never drives the agent threads.

Run:  ./.venv/bin/streamlit run app.py
"""
from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

from runner import Orchestrator
from evaluate import evaluate

load_dotenv()

st.set_page_config(page_title="The Governor", layout="wide", page_icon="🛡️")

# ---- session state ----
if "orc" not in st.session_state:
    st.session_state.orc = None

# ---- header ----
st.title("🛡️ The Governor")
st.caption(
    "The oversight & eval layer for autonomous recruiting agents. "
    "5 Fillmore-style sourcing agents propose outreach; the Governor decides which sends "
    "a human must see — reserving scarce human attention for the calls that need judgment."
)

# ---- sidebar controls ----
with st.sidebar:
    st.header("Controls")
    mode = st.radio("Mode", ["governor", "fifo"],
                    format_func=lambda m: "🛡️ Governor (judgment layer)" if m == "governor"
                    else "📥 FIFO (every send hits the human)")
    source = st.radio(
        "Candidate source",
        ["labeled", "discovered", "agents"],
        format_func=lambda s: {
            "labeled": "🏷️ Labeled set (scoreboard proof)",
            "discovered": "🌐 Discovered (one shared search)",
            "agents": "🤖 Autonomous agents (each its own territory)",
        }[s],
        help="labeled = the 40 hand-labeled cases the eval scoreboard is built on. "
             "discovered = one shared GitHub search feeding all agents (a pipeline). "
             "agents = Level 2: each of the 5 agents autonomously searches its OWN "
             "territory, drafts, self-critiques, and proposes. Discovered/agent leads have "
             "no ground-truth labels, so they don't feed the correctness metrics.",
    )
    if source == "discovered":
        if st.button("🔎 Discover now (live GitHub search)", use_container_width=True):
            with st.spinner("Searching GitHub for real candidates matching the brief…"):
                from discover import search_candidates, save, BRIEF
                try:
                    cands = search_candidates(BRIEF, limit=10)
                    save(cands)
                    st.session_state.discovered_preview = [
                        (c.name, c.current_company, c.match_confidence) for c in cands]
                    st.success(f"Discovered {len(cands)} real candidates → cached.")
                except Exception as e:
                    st.error(f"Discovery failed ({str(e)[:100]}). Cached set (if any) still used.")
        for name, co, m in st.session_state.get("discovered_preview", [])[:10]:
            st.caption(f"• {name} @ {co} — match {m:.2f}")
    elif source == "agents":
        st.caption("Each agent owns a territory and searches it live. Pre-warm caches so the "
                   "stage run replays offline:")
        if st.button("🔎 Discover all territories (live)", use_container_width=True):
            from territories import TERRITORIES
            from discover import search_by_query
            prev = []
            with st.spinner("5 agents searching their territories on GitHub…"):
                for terr in TERRITORIES:
                    try:
                        cands = search_by_query(terr["query"], limit=4)
                        prev.append(f"✅ {terr['goal']}: {len(cands)} leads")
                    except Exception as e:
                        prev.append(f"⚠ {terr['goal']}: {str(e)[:40]}")
            st.session_state.territory_preview = prev
            st.success("Territories cached — offline replay ready.")
        for line in st.session_state.get("territory_preview", []):
            st.caption(line)
    zero_mode = st.radio("Zero.xyz send", ["stub", "real"],
                         help="stub = demo-safe, no network. real = actually send via Zero.xyz.")
    use_llm = st.toggle(
        "🧠 Live LLM drafting (AWS Bedrock)",
        value=False,
        help="ON = each agent drafts the email via real Claude on Bedrock (needs AWS creds in .env). "
             "OFF = prewritten drafts. Flip OFF instantly if creds/network flake on stage — "
             "it's a demo-control switch, not an error safety net (that fallback is automatic).",
    )
    speed = st.slider("Agent step delay (s)", 0.05, 1.0, 0.25, 0.05)
    c1, c2 = st.columns(2)
    if c1.button("▶ Start", use_container_width=True, type="primary"):
        orc = Orchestrator(mode=mode, zero_mode=zero_mode, step_delay=speed,
                           use_llm=use_llm, source=source)
        orc.start()
        st.session_state.orc = orc
    if c2.button("⟲ Reset", use_container_width=True):
        if st.session_state.orc:
            st.session_state.orc.stop()
        st.session_state.orc = None
    st.divider()
    st.markdown("**Sponsors:** AWS Bedrock · Zero.xyz · Akash")
    if use_llm:
        if os.environ.get("ANTHROPIC_API_KEY"):
            st.success("🧠 Live drafting ON via **Anthropic API** — agents draft with real "
                       "Claude (auto-falls back to templates on any error).")
        elif os.environ.get("AWS_ACCESS_KEY_ID"):
            st.success("🧠 Live drafting ON via **AWS Bedrock** — agents draft with real Claude.")
        else:
            st.warning("Live LLM drafting is ON but no key found. Add ANTHROPIC_API_KEY "
                       "(or AWS creds) to .env — otherwise agents fall back to templates.")
    if zero_mode == "real" and not os.environ.get("ZERO_API_URL"):
        st.warning("Zero real mode set but ZERO_API_URL missing — sends fall back to stub.")


def _badge(decision: str) -> str:
    return {"auto_send": "🟢 auto-sent", "escalate": "🟠 escalated",
            "hold": "🔵 held (load-shed)"}.get(decision, decision)


@st.fragment(run_every=1.0)
def live_dashboard():
    orc: Orchestrator | None = st.session_state.orc
    if orc is None:
        st.info("Pick a mode in the sidebar and hit **Start**. "
                "Try **FIFO** first (human drowns), then **Governor** (judgment layer).")
        return

    snap = orc.snapshot()

    # ---- counters ----
    m = st.columns(5)
    m[0].metric("Autonomy", f"{snap['autonomy_pct']:.0f}%")
    m[1].metric("Human-facing", f"{snap['human_facing']}", help="requests that reached a human")
    m[2].metric("Queue depth", snap["queue_depth"])
    m[3].metric("Auto-sent", snap["auto_sent"])
    dang = snap["dangerous_auto_sends"]
    m[4].metric("⚠ Dangerous auto-sends", dang, delta="target 0",
                delta_color="inverse" if dang else "off")
    st.progress(snap["processed"] / max(1, snap["total"]),
                text=f"processed {snap['processed']}/{snap['total']}")
    if dang:
        st.error(f"{dang} risky email(s) were auto-sent without a human — this is what FIFO-less "
                 f"autonomy risks. The Governor's job is to keep this at 0.")

    left, right = st.columns([1, 1.4])

    # ---- agents ----
    with left:
        st.subheader("Sourcing agents")
        for aid, status in snap["agent_status"].items():
            icon = "✅" if status == "done" else ("💤" if status == "idle" else "⚙️")
            st.write(f"{icon} **{aid}** — {status}")
        st.divider()
        st.subheader("Auto-handled")
        st.write(f"🟢 auto-sent: **{snap['auto_sent']}**   🔵 held: **{len(snap['held'])}**")
        with st.expander("held (deferred to protect human capacity)"):
            for it in snap["held"]:
                st.caption(f"{it.action.candidate.name} @ {it.action.candidate.current_company} "
                           f"— risk {it.risk:.2f} — {it.reasons[0] if it.reasons else ''}")

    # ---- queue ----
    with right:
        st.subheader(f"Approval queue ({snap['queue_depth']})")
        if not snap["human_queue"]:
            st.caption("empty — nothing needs a human right now.")
        for it in snap["human_queue"]:
            c = it.action.candidate
            with st.container(border=True):
                top = st.columns([3, 1])
                top[0].markdown(f"**{c.name}** — {c.current_title} @ {c.current_company}")
                top[1].markdown(f"risk **{it.risk:.2f}**")
                st.caption(f"{_badge(it.decision)} · {', '.join(it.reasons[:2])}")
                st.text(f"“{it.action.subject}” — {it.action.body[:90]}…")
                b = st.columns(2)
                if b[0].button("✅ Approve & send", key=f"a{it.action.seq}", use_container_width=True):
                    orc.approve(it.action.seq)
                    st.rerun()
                if b[1].button("🚫 Deny", key=f"d{it.action.seq}", use_container_width=True):
                    orc.deny(it.action.seq)
                    st.rerun()

    with st.expander("recent events"):
        for e in reversed(snap["events"]):
            st.caption(e)


live_dashboard()

# ---- eval scoreboard (static proof) ----
st.divider()
st.subheader("📊 Eval scoreboard — is the judgment correct?")
st.caption("The Governor's policy run against a hand-labeled ground-truth set. "
           "Not 'fewer approvals' — *measured* correctness.")
sb = evaluate()
e = st.columns(4)
e[0].metric("Dangerous auto-sends", sb.false_auto_send, delta="target 0",
            delta_color="inverse" if sb.false_auto_send else "off")
e[1].metric("Escalation recall", f"{sb.escalation_recall:.2f}", help="fraction of real risks caught")
e[2].metric("Escalation precision", f"{sb.escalation_precision:.2f}")
e[3].metric("Human-load saved", f"{sb.human_load_saved_pct:.0f}%")
st.code(sb.pretty(), language="text")
st.caption("Note: the labeled set is co-designed with the policy, so separation is expected — "
           "the honest next step is held-out / adversarial eval. (A good thing to say out loud.)")
