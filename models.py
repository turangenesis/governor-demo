"""Shared data models. Reused by the agent, the Governor, the eval harness, and the UI.

Deliberately plain dataclasses — no framework coupling, easy to serialize for the queue.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


@dataclass
class Candidate:
    """A sourcing target. `matched` = how well they fit the role (agent/data-layer supplied)."""
    name: str
    current_company: str
    current_title: str
    seniority: str            # e.g. "junior" | "mid" | "senior" | "exec"
    email: str
    matched_role: str
    match_confidence: float   # 0..1 how well the profile fits the role


@dataclass
class HiringBrief:
    role: str
    must_haves: str
    hiring_company: str
    competitors: tuple[str, ...] = ()   # companies we must NOT poach from without a human


@dataclass
class ProposedAction:
    """What an agent wants to do: send this outreach email. The Governor decides its fate."""
    agent_id: str
    candidate: Candidate
    subject: str
    body: str
    draft_confidence: float             # 0..1 the agent's own confidence in the draft
    concerns: list[str] = field(default_factory=list)  # agent-surfaced worries
    seq: int = 0                        # arrival order (for FIFO)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class Decision(str, Enum):
    AUTO_SEND = "auto_send"       # safe enough to fire without a human
    ESCALATE = "escalate"         # genuinely needs a human recruiter's judgment
    HOLD = "hold"                 # load-shed: human is saturated, park the low-risk marginal case


@dataclass
class GovernorDecision:
    decision: Decision
    risk_score: float             # 0..1 aggregate risk
    reasons: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)   # named risk signals -> value/weight
