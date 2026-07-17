"""Step 1: one Fillmore-style sourcing agent as a LangGraph graph on AWS Bedrock.

The agent reads a candidate + hiring brief, drafts an outreach email, self-checks it,
and emits a ProposedAction (send this email) for the Governor to judge.

Uses LangGraph built-ins (StateGraph) — no hand-rolled while-loop.

Run:
    ./.venv/bin/python agent.py            # real Bedrock (needs AWS creds in .env)
    ./.venv/bin/python agent.py --dry      # fake LLM, verifies graph wiring w/o creds
"""
from __future__ import annotations

import json
import os
import sys
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END

from models import Candidate, HiringBrief, ProposedAction

load_dotenv()

DRAFT_SYSTEM = """You are a sourcing agent writing a short, warm, specific cold-outreach email \
to a candidate about a role. Keep it under 120 words. Be human, not salesy. \
Return ONLY valid JSON with keys: subject (str), body (str), \
draft_confidence (float 0..1, your honest confidence this outreach is on-target and appropriate), \
concerns (list of short strings — anything that might make a human recruiter want to review this)."""


class AgentState(TypedDict, total=False):
    candidate: Candidate
    brief: HiringBrief
    subject: str
    body: str
    draft_confidence: float
    concerns: list
    log: list


def _draft_prompt(c: Candidate, b: HiringBrief) -> str:
    return (
        f"ROLE: {b.role} at {b.hiring_company}\n"
        f"MUST-HAVES: {b.must_haves}\n"
        f"CANDIDATE: {c.name}, {c.current_title} at {c.current_company} "
        f"(seniority: {c.seniority}); profile match {c.match_confidence:.2f} for {c.matched_role}.\n"
        f"Write the outreach email as JSON."
    )


def _parse_json(text: str) -> dict:
    """Bedrock/Claude sometimes wraps JSON in prose or fences — extract the object."""
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1].removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def make_llm():
    """Real Bedrock LLM. Imported lazily so --dry works with zero AWS setup."""
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        temperature=0.4,
        max_tokens=600,
    )


class _FakeLLM:
    """Deterministic stand-in so we can verify the graph without Bedrock creds."""

    def invoke(self, prompt: str):
        class _R:
            content = json.dumps(
                {
                    "subject": "Quick question about your work",
                    "body": "Hi there — I came across your profile and was impressed. "
                    "We're hiring and I'd love to chat. Open to a quick call?",
                    "draft_confidence": 0.82,
                    "concerns": [],
                }
            )

        return _R()


def build_graph(llm):
    """Compile the single-agent graph: draft -> self_check -> END."""

    def draft_node(state: AgentState) -> AgentState:
        c, b = state["candidate"], state["brief"]
        resp = llm.invoke(DRAFT_SYSTEM + "\n\n" + _draft_prompt(c, b))
        content = resp.content if hasattr(resp, "content") else str(resp)
        if isinstance(content, list):  # some Bedrock responses return content blocks
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        data = _parse_json(content)
        return {
            "subject": data.get("subject", "(no subject)"),
            "body": data.get("body", ""),
            "draft_confidence": float(data.get("draft_confidence", 0.5)),
            "concerns": list(data.get("concerns", [])),
            "log": state.get("log", []) + ["drafted outreach"],
        }

    def self_check_node(state: AgentState) -> AgentState:
        # Cheap deterministic guard: empty/very short body => low confidence + a concern.
        concerns = list(state.get("concerns", []))
        conf = state.get("draft_confidence", 0.5)
        if len(state.get("body", "")) < 40:
            concerns.append("draft too short / low quality")
            conf = min(conf, 0.4)
        return {"concerns": concerns, "draft_confidence": conf,
                "log": state.get("log", []) + ["self-checked"]}

    g = StateGraph(AgentState)
    g.add_node("draft", draft_node)
    g.add_node("self_check", self_check_node)
    g.add_edge(START, "draft")
    g.add_edge("draft", "self_check")
    g.add_edge("self_check", END)
    return g.compile()


def run_once(candidate: Candidate, brief: HiringBrief, llm, agent_id: str = "agent-1", seq: int = 0) -> ProposedAction:
    graph = build_graph(llm)
    out = graph.invoke({"candidate": candidate, "brief": brief, "log": []})
    return ProposedAction(
        agent_id=agent_id,
        candidate=candidate,
        subject=out["subject"],
        body=out["body"],
        draft_confidence=out["draft_confidence"],
        concerns=out["concerns"],
        seq=seq,
    )


# --- demo fixtures ---
DEMO_BRIEF = HiringBrief(
    role="Senior Backend Engineer",
    must_haves="Python, distributed systems, 5+ yrs",
    hiring_company="Acme AI",
    competitors=("OpenAI", "Anthropic", "Google DeepMind"),
)
DEMO_CANDIDATE = Candidate(
    name="Jordan Lee",
    current_company="Stripe",
    current_title="Staff Engineer",
    seniority="senior",
    email="jordan.lee@example.com",
    matched_role="Senior Backend Engineer",
    match_confidence=0.88,
)


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    llm = _FakeLLM() if dry else make_llm()
    print(f"=== Sourcing agent — {'DRY (fake LLM)' if dry else 'Bedrock'} ===")
    action = run_once(DEMO_CANDIDATE, DEMO_BRIEF, llm)
    print(f"\nTo: {action.candidate.name} <{action.candidate.email}>")
    print(f"Subject: {action.subject}\n")
    print(action.body)
    print(f"\ndraft_confidence: {action.draft_confidence:.2f}")
    print(f"concerns: {action.concerns or 'none'}")
