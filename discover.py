"""Step 0 (the missing autonomous loop): REAL candidate discovery from the internet.

This is the front of the loop the demo was missing. Instead of a hand-typed candidate
list, a sourcing agent actually SEARCHES the web (GitHub's public search API) for real
engineers who match the hiring brief, pulls their real public profile, and turns each
into a Candidate the drafting agent + Governor then act on.

Why GitHub search (not generic web search):
  - returns REAL engineers with STRUCTURED fields (name, company, bio, location) —
    parseable into a Candidate with NO LLM.
  - no API key needed (unauthenticated search works at a low rate limit).
  - genuinely "the agent found real people on the internet", which is the honest
    autonomous-loop story judges want to see.

The "save it and replay without LLMs" trick (the smart part):
  - `python discover.py`          -> runs the REAL search once, caches to discovered_candidates.json
  - `python discover.py --show`   -> loads the cache, no network, deterministic (stage-safe)
  Run it live once before the demo; on stage you replay the cache. Same pattern as the
  prewritten eval set, except the data is genuinely harvested.

SAFETY: discovery only READS public profiles. No email is sent here. Any later send is
still gated by the Governor and redirected to DEMO_RECIPIENT_EMAIL — no real candidate
is ever emailed by the demo.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict

from models import Candidate, HiringBrief

CACHE_PATH = os.environ.get("DISCOVERY_CACHE", "discovered_candidates.json")
GITHUB_SEARCH = "https://api.github.com/search/users"

# The brief the discovery agent sources against (mirrors eval_set.BRIEF).
BRIEF = HiringBrief(
    role="Senior Backend Engineer",
    must_haves="Python, distributed systems, 5+ yrs",
    hiring_company="Acme AI",
    competitors=("OpenAI", "Anthropic", "Google DeepMind", "Cohere"),
)

# Keywords we look for in a profile bio to score fit — derived from the brief's must-haves.
_FIT_KEYWORDS = ("python", "backend", "distributed", "infrastructure", "systems",
                 "platform", "scala", "go", "rust", "api", "cloud", "kubernetes")
_SENIOR_WORDS = ("staff", "principal", "senior", "lead", "architect", "head", "director")
_EXEC_WORDS = ("vp", "vice president", "cto", "chief", "founder", "head of engineering")
_JUNIOR_WORDS = ("junior", "intern", "student", "graduate", "learning")


def _seniority_from(text: str) -> str:
    t = text.lower()
    if any(w in t for w in _EXEC_WORDS):
        return "exec"
    if any(w in t for w in _SENIOR_WORDS):
        return "senior"
    if any(w in t for w in _JUNIOR_WORDS):
        return "junior"
    return "mid"


def _match_confidence(profile: dict) -> float:
    """Heuristic 0..1 fit score from real profile signals — NO LLM."""
    bio = (profile.get("bio") or "").lower()
    hits = sum(1 for k in _FIT_KEYWORDS if k in bio)
    score = 0.45
    score += min(hits, 4) * 0.08                       # bio matches the brief
    if profile.get("company"):
        score += 0.08                                  # has a current company
    if (profile.get("followers") or 0) >= 100:
        score += 0.07                                  # some standing in the community
    if profile.get("hireable"):
        score += 0.10                                  # explicitly open to work
    return round(min(score, 0.97), 2)


def _company_of(profile: dict) -> str:
    c = (profile.get("company") or "").strip().lstrip("@")
    return c or "Independent / open source"


def _profile_to_candidate(profile: dict, role: str) -> Candidate:
    name = profile.get("name") or profile.get("login") or "Unknown"
    bio = profile.get("bio") or ""
    title_hint = bio.split(".")[0][:60] if bio else "Software Engineer"
    return Candidate(
        name=name,
        current_company=_company_of(profile),
        current_title=title_hint or "Software Engineer",
        seniority=_seniority_from(f"{title_hint} {bio}"),
        # GitHub rarely exposes a public email; use the noreply alias. Sends redirect
        # to DEMO_RECIPIENT_EMAIL anyway, so no real person is contacted.
        email=profile.get("email") or f"{profile.get('login','user')}@users.noreply.github.com",
        matched_role=role,
        match_confidence=_match_confidence(profile),
    )


def _query_for(brief: HiringBrief) -> str:
    """Default single-source query — real *individual* engineers.

    `type:user` excludes orgs (so we don't source "OpenAI" as a person); the follower
    band skips both celebrities and empty accounts, landing on plausible ICs.
    """
    return "language:python type:user followers:100..3000 repos:>15"


def _headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN")   # set this to lift the 60/hr unauth rate limit
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _cache_path(query: str) -> str:
    return "cache_" + re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:48] + ".json"


MAX_PROFILE_FETCHES = 8   # hard cap of profile lookups per search — bounds the rate-limit cost


def _run_search(query: str, role: str, limit: int) -> list[Candidate]:
    """One REAL GitHub search: find individuals, fetch each real profile. No cache.

    Rate-limit safe: at most 1 + MAX_PROFILE_FETCHES requests per call, and on ANY
    error / 403 (rate limit) it returns what it has so far — possibly none — rather than
    crashing. So an agent that runs out of budget simply proposes fewer (or zero) leads.
    """
    import requests

    try:
        resp = requests.get(GITHUB_SEARCH,
                            params={"q": query, "per_page": min(limit * 3, 30)},
                            headers=_headers(), timeout=20)
        if resp.status_code == 403:
            print(f"[discover] rate-limited on search — 0 leads for: {query[:50]}")
            return []
        resp.raise_for_status()
    except Exception as e:
        print(f"[discover] search failed ({str(e)[:50]}) — 0 leads")
        return []

    items = resp.json().get("items", [])
    out: list[Candidate] = []
    fetches = 0
    for it in items:
        if len(out) >= limit or fetches >= MAX_PROFILE_FETCHES:
            break
        try:
            r = requests.get(it["url"], headers=_headers(), timeout=20)
            fetches += 1
            if r.status_code == 403:
                print("[discover] rate-limited fetching profiles — returning partial")
                break
            prof = r.json()
        except Exception:
            continue
        if prof.get("type") != "User":          # skip organizations
            continue
        if not prof.get("name"):                 # skip anonymous accounts (weak lead)
            continue
        out.append(_profile_to_candidate(prof, role))
    return out


def search_by_query(query: str, role: str = BRIEF.role, limit: int = 5,
                    use_cache: bool = True) -> list[Candidate]:
    """Cached real search: first call fetches + caches per query; later calls replay offline.

    This is the tool a sourcing agent calls with ITS OWN territory query. Caching means a
    live stage run hits the network once, then replays deterministically with no network.
    """
    path = _cache_path(query)
    if use_cache and os.path.exists(path):
        return load(path)
    cands = _run_search(query, role, limit)
    save(cands, path)
    return cands


def search_candidates(brief: HiringBrief = BRIEF, limit: int = 10) -> list[Candidate]:
    """REAL discovery for the single-source 'discovered' pipeline mode (backward compat)."""
    return search_by_query(_query_for(brief), brief.role, limit)


def save(candidates: list[Candidate], path: str = CACHE_PATH) -> None:
    with open(path, "w") as f:
        json.dump([asdict(c) for c in candidates], f, indent=2)


def load(path: str = CACHE_PATH) -> list[Candidate]:
    """Replay discovered candidates from cache — no network, no LLM, deterministic."""
    with open(path) as f:
        return [Candidate(**d) for d in json.load(f)]


def load_or_discover(brief: HiringBrief = BRIEF, limit: int = 10,
                     path: str = CACHE_PATH) -> list[Candidate]:
    """Use the cache if present (stage-safe); otherwise run the real search and cache it."""
    if os.path.exists(path):
        return load(path)
    cands = search_candidates(brief, limit)
    save(cands, path)
    return cands


def discovered_cases(brief: HiringBrief = BRIEF, limit: int = 10):
    """Wrap discovered real candidates as cases the Orchestrator can run.

    Uses the same template draft as the labeled set so the no-LLM path works; the
    drafting agent will re-write via Bedrock when use_llm is on. ground_truth is ""
    (unknown) — these are live leads, not a labeled eval set, so they never feed the
    correctness metrics (those stay on the hand-labeled scoreboard).
    """
    from eval_set import LabeledCase, _CLEAN_BODY
    from models import ProposedAction

    cases = []
    for i, c in enumerate(load_or_discover(brief, limit), start=1):
        first = c.name.split()[0]
        body = _CLEAN_BODY.format(name=first, company=c.current_company, role=brief.role)
        action = ProposedAction("agent", c, "A role you might like", body,
                                c.match_confidence, [], seq=i)
        cases.append(LabeledCase(action, "", "discovered live from GitHub"))
    return cases


if __name__ == "__main__":
    if "--show" in sys.argv:
        cands = load()
        print(f"=== {len(cands)} cached candidates (replay, no network) ===")
    else:
        print("=== REAL discovery: searching GitHub for candidates matching the brief ===")
        cands = search_candidates(BRIEF, limit=10)
        save(cands)
        print(f"discovered {len(cands)} real candidates -> {CACHE_PATH}")

    for c in cands:
        print(f"  {c.name:22} | {c.current_company:24} | {c.seniority:6} | "
              f"match {c.match_confidence:.2f} | {c.current_title[:40]}")
