from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.config import Profile
from jobfinder.db.models import Pipeline, Posting, Score
from jobfinder.db.session import release_snapshot
from jobfinder.llm.base import LLMError, LLMProvider, load_prompt, load_schema
from jobfinder.scoring.resume_summary import build_resume_summary
from jobfinder.tailoring.master_schema import MasterResume, load_master, master_exists

log = logging.getLogger(__name__)
MAX_DESCRIPTION_CHARS = 6000


def _posting_block(posting: Posting) -> str:
    where = " / ".join(
        x for x in [posting.location_raw, posting.remote_type, posting.remote_scope] if x
    )
    sources = ", ".join(s.source for s in posting.sources) or "n/a"
    return (
        f"POSTING\nTitle: {posting.title}\nCompany: {posting.company.name}\nLocation: {where}\n"
        f"Salary: {posting.salary_raw or 'n/a'}\nSource: {sources}\n\n"
        f"{posting.description_text[:MAX_DESCRIPTION_CHARS]}"
    )


def _candidate_block(profile: Profile) -> str:
    """Where the candidate may work, from the profile (the resume summary is contact-free and
    says nothing about it). Without this the model docks every posting outside Canada."""
    locs = ", ".join(
        f"{loc.key} ({loc.country or '/'.join(loc.remote_scope) or 'any'})"
        for loc in profile.enabled_locations()
    )
    lines = ["CANDIDATE ELIGIBILITY", f"Enabled locations (all acceptable): {locs or 'none'}"]
    if profile.scoring.candidate_note.strip():
        lines.append(f"Note: {profile.scoring.candidate_note.strip()}")
    return "\n".join(lines)


def score_posting(
    session: Session,
    posting: Posting,
    *,
    master: MasterResume,
    llm: LLMProvider,
    profile: Profile,
    summary: str | None = None,
) -> Score:
    summary = summary or build_resume_summary(master)
    user = f"CANDIDATE\n{summary}\n\n{_candidate_block(profile)}\n\n{_posting_block(posting)}"
    # The LLM call takes minutes; the writes below must not start on the read snapshot
    # opened while building the prompt (2026-09-09: "database is locked" at 07:01:48
    # because the digest committed its Run row at 07:00 during a scoring call).
    release_snapshot(session)
    data = llm.complete_json(
        task="score_posting",
        system=load_prompt("score_posting"),
        user=user,
        schema=load_schema("score"),
        tier="fast",
        language=posting.language,
    )
    score = Score(
        posting_id=posting.id,
        model=getattr(llm, "name", "unknown"),
        fit_score=int(data["fit_score"]),
        reasons=data["reasons"],
        missing_requirements=data["missing_requirements"],
        seniority_match=data["seniority_match"],
        eligibility=data["eligibility"],
        red_flags=data["red_flags"],
        one_line_summary=data["one_line_summary"],
        raw=data,
    )
    session.add(score)
    if data["language"] in ("en", "fr"):
        posting.language = data["language"]
    posting.prefilter_result = {
        k: v for k, v in (posting.prefilter_result or {}).items()
        if k not in ("rescore", "needs_review")
    }
    if score.fit_score >= profile.scoring.match_threshold:
        posting.status = "match"
        if posting.pipeline is None:
            posting.pipeline = Pipeline(stage="new")
    else:
        posting.status = "scored"
    session.flush()
    return score


def select_candidates(
    session: Session, limit: int, profile: Profile | None = None
) -> list[Posting]:
    """Postings to score this run, at most `limit`. With a profile, enabled-location order
    comes first (Canada before uk), so one country's backlog cannot consume the whole
    per-run budget; within a location, flagged re-scores, then highest keyword overlap."""
    rows = session.scalars(
        select(Posting).where(
            Posting.status.in_(("new", "scored", "match")),
            Posting.prefilter_result.is_not(None),
        )
    ).all()

    def eligible(p: Posting) -> bool:
        pr = p.prefilter_result or {}
        if pr.get("rescore"):
            return True
        # Controller decision (Plan 1 final review, I6): score only fully prefiltered, hydrated
        # postings. Snippet-only survivors carry stage == "pass1" and description_complete False.
        return (
            p.status == "new"
            and pr.get("passed") is True
            and pr.get("stage", "pass2") == "pass2"
            and p.description_complete
            and "needs_review" not in pr
        )

    picked = [p for p in rows if eligible(p)]

    def rank(p: Posting) -> int:
        if profile is None:
            return 0
        return profile.location_rank((p.prefilter_result or {}).get("location_key"))

    picked.sort(
        key=lambda p: (
            rank(p),
            not (p.prefilter_result or {}).get("rescore"),
            -((p.prefilter_result or {}).get("overlap") or 0),
            p.id,
        )
    )
    return picked[:limit]


def score_new_postings(
    session: Session,
    *,
    llm: LLMProvider,
    profile: Profile,
    master: MasterResume | None = None,
    limit: int | None = None,
    max_consecutive_failures: int = 3,
) -> dict:
    """Score this run's candidates. Stops after `max_consecutive_failures` LLM failures in a
    row (`stats["aborted"]`): offline, every `claude -p` call takes minutes to fail, and on
    2026-09-09 that turned a 40-minute scan into a two-hour one."""
    stats = {"scored": 0, "matches": 0, "needs_review": 0, "skipped_no_master": 0}
    limit = limit or profile.scoring.max_llm_scored_per_run
    candidates = select_candidates(session, limit, profile)
    if master is None:
        if not master_exists():
            stats["skipped_no_master"] = len(candidates)
            return stats
        master = load_master()
    summary = build_resume_summary(master)
    streak = 0
    for posting in candidates:
        try:
            score_posting(
                session, posting, master=master, llm=llm, profile=profile, summary=summary
            )
        except LLMError as exc:
            posting.prefilter_result = {
                **(posting.prefilter_result or {}), "needs_review": "llm_output"
            }
            stats["needs_review"] += 1
            log.warning("scoring failed for posting %s: %s", posting.id, exc)
            session.commit()
            streak += 1
            if streak >= max_consecutive_failures:
                stats["aborted"] = f"{streak} consecutive LLM failures"
                log.warning("scoring aborted for this run: %s", stats["aborted"])
                break
            continue
        streak = 0
        stats["scored"] += 1
        if posting.status == "match":
            stats["matches"] += 1
        session.commit()
    return stats
