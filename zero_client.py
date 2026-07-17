"""Step 3: Zero.xyz send client — the real-world action layer the Governor gates.

Two modes (ZERO_MODE in .env):
  - "stub"  : demo-safe. Logs the send, returns a fake id. Never touches the network.
              This is the default so a flaky network can never kill the live demo.
  - "real"  : actually send via Zero.xyz. Because Zero is an agent-activation layer
              (your agent discovers a service and calls it), the concrete send endpoint
              depends on your Zero setup — wire it in _send_real() once your account is up.
              On ANY error it falls back to stub so the demo survives.

SAFETY: only the $5 Zero starting credit; never fund the wallet. Every call here is
reached ONLY after the Governor decided AUTO_SEND or a human approved an ESCALATE.
"""
from __future__ import annotations

import os
from dotenv import load_dotenv

from models import ProposedAction

load_dotenv()


def _demo_recipient(action: ProposedAction) -> str:
    """During a live demo, redirect all sends to YOUR inbox so no real candidate is emailed."""
    return os.environ.get("DEMO_RECIPIENT_EMAIL") or action.candidate.email


def _send_stub(action: ProposedAction) -> dict:
    to = _demo_recipient(action)
    fake_id = f"stub-{action.agent_id}-{action.seq}"
    return {"status": "stubbed", "to": to, "subject": action.subject, "id": fake_id, "via": "stub"}


def _send_real(action: ProposedAction) -> dict:
    """Real send via Zero.xyz.

    Zero routes your agent to a real email service. If you have a concrete Zero HTTP
    endpoint, set ZERO_API_URL + ZERO_API_KEY in .env and this will POST to it.
    """
    api_url = os.environ.get("ZERO_API_URL")
    api_key = os.environ.get("ZERO_API_KEY")
    if not api_url or not api_key:
        raise RuntimeError("ZERO_API_URL/ZERO_API_KEY not set — cannot send real; will fall back to stub")

    import requests

    payload = {
        "to": _demo_recipient(action),
        "subject": action.subject,
        "body": action.body,
    }
    resp = requests.post(
        api_url,
        json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return {"status": "sent", "to": payload["to"], "subject": action.subject,
            "id": data.get("id", "zero-ok"), "via": "zero.xyz"}


def send_outreach(action: ProposedAction, mode: str | None = None) -> dict:
    """Send the gated outreach. Returns a result dict. Falls back to stub on any real-send error."""
    mode = (mode or os.environ.get("ZERO_MODE", "stub")).lower()
    if mode != "real":
        return _send_stub(action)
    try:
        return _send_real(action)
    except Exception as e:  # demo must never die on a network hiccup
        result = _send_stub(action)
        result["fell_back"] = True
        result["error"] = str(e)[:160]
        return result


if __name__ == "__main__":
    from agent import DEMO_CANDIDATE
    from models import ProposedAction as PA

    a = PA("agent-1", DEMO_CANDIDATE, "A role you might like", "Hi Jordan, quick chat about a role?", 0.9)
    print("stub:", send_outreach(a, mode="stub"))
    print("real (no endpoint -> falls back):", send_outreach(a, mode="real"))
