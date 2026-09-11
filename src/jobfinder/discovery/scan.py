from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.config import Profile
from jobfinder.db.models import Company, Posting, Run, utcnow
from jobfinder.db.session import release_snapshot
from jobfinder.discovery.base import JobSource, SearchProfile
from jobfinder.discovery.dedupe import upsert_raw_postings
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient, RateLimited
from jobfinder.discovery.health import AdapterGuard
from jobfinder.discovery.hydrate import hydrate_posting
from jobfinder.discovery.prefilter import prefilter
from jobfinder.discovery.registry import build_sources
from jobfinder.settings import Settings
from jobfinder.tailoring.master_schema import load_master, master_exists

log = logging.getLogger(__name__)
Scorer = Callable[[Session], dict]

# Injected so tests can monkeypatch the pacing sleep between same-host hydration
# requests without actually waiting.
_sleep = time.sleep


def _cap_key(name: str) -> str:
    return "ats_boards" if name.startswith("ats_") else name


def ready_to_score(posting: Posting) -> bool:
    """Whether a posting may go to the LLM scorer (the controller's Plan-1 decision).

    Three conditions, all required: it is still `new`, it passed the prefilter, and its
    description is complete — a snippet-only posting is deliberately not scored, it waits
    for hydration (or for hydration to give up, which completes it). `prefilter_result`'s
    "stage" key says which of the two prefilter passes produced the stored result:
    "pass1" = snippet-only, "pass2" = filtered against the full description.
    """
    result = posting.prefilter_result or {}
    return (
        posting.status == "new"
        and result.get("passed") is True
        and bool(posting.description_complete)
    )


def run_scan(
    session: Session,
    *,
    profile: Profile,
    settings: Settings,
    llm=None,
    no_llm: bool = False,
    client: HttpClient | None = None,
    sources: list[JobSource] | None = None,
    scorer: Scorer | None = None,
    contacts: Scorer | None = None,
) -> Run:
    run = Run(kind="scan")
    session.add(run)
    session.commit()
    run_id = run.id
    client = client or HttpClient()
    sp = SearchProfile.from_profile(profile)
    stats: dict = {
        "sources": {}, "skipped": {}, "errors": {}, "upsert": {}, "prefilter": {},
        # hydrated: fetched this run. hydrate_capped: skipped by max_hydrate_per_run.
        # hydrate_deferred: skipped because their host (or the call budget) stopped.
        "hydrated": 0, "hydrate_capped": 0, "hydrate_deferred": 0,
    }

    try:
        if sources is None:
            def companies_for(ats: str) -> list[Company]:
                return list(session.scalars(select(Company).where(Company.ats_type == ats)))
            sources, stats["skipped"] = build_sources(profile, settings, client, companies_for)

        for source in sources:
            # The daily CAP is pooled across ats_* adapters, but health (cooldown, last
            # error, last ok) is per source: one board's 429 must not bench the others.
            cap_key = _cap_key(source.name)
            guard = AdapterGuard(
                session,
                source.name,
                profile.source_daily_cap(cap_key),
                budget_adapter=cap_key if cap_key != source.name else None,
            )
            ok, why = guard.allowed()
            if not ok:
                stats["skipped"][source.name] = why
                continue
            row = guard.row()
            since = (row.last_ok_at - timedelta(days=1)) if row.last_ok_at else (
                utcnow().replace(tzinfo=None) - timedelta(days=sp.max_days_old)
            )
            client.set_call_budget(guard.remaining())
            # Slow HTTP call ahead: end the read transaction so the upsert after it does
            # not start on a snapshot another thread's commit has made stale.
            release_snapshot(session)
            try:
                raws = source.fetch(sp, since)
            except RateLimited as exc:
                guard.add_calls(client.calls_made)
                guard.record_error(str(exc), exc.retry_after)
                stats["errors"][source.name] = str(exc)
                continue
            except Exception as exc:  # noqa: BLE001 — one adapter never stops the run
                guard.add_calls(client.calls_made)
                guard.record_error(repr(exc)[:500], 3600)
                stats["errors"][source.name] = repr(exc)[:200]
                log.exception("source %s failed", source.name)
                continue
            guard.add_calls(client.calls_made)
            guard.record_ok()
            stats["sources"][source.name] = len(raws)
            try:
                upsert_stats = upsert_raw_postings(session, raws, max_age_days=sp.max_days_old)
                stats["upsert"][source.name] = asdict(upsert_stats)
            except Exception as exc:  # noqa: BLE001 — bad data from one source never aborts the run
                session.rollback()
                stats["errors"][source.name] = repr(exc)[:200]
                log.exception("upsert failed for source %s", source.name)
                continue

        client.set_call_budget(None)
        skill_terms = load_master().overlap_terms() if master_exists() else set()
        # A posting that couldn't be hydrated last run (capped, deferred, or
        # budget-exceeded) still has a prefilter_result — the pass-1-only result, marked
        # `stage="pass1"` — so `prefilter_result IS NULL` alone would wrongly treat it as
        # fully handled and never retry hydration. Filter in Python (not JSON-in-SQL) to
        # catch both "never filtered" and "only pass-1 filtered" rows portably.
        fresh_q = select(Posting).where(Posting.status == "new")
        fresh = [
            p for p in session.scalars(fresh_q)
            if (p.prefilter_result or {}).get("stage") != "pass2"
        ]
        reasons: Counter[str] = Counter()
        survivors: list[Posting] = []
        location_rank: dict[int, int] = {}
        for p in fresh:
            r = prefilter(p, profile, skill_terms, check_overlap=False)
            if not r.passed:
                p.status, p.prefilter_result = "prefiltered_out", r.as_dict()
                reasons[r.reason] += 1
            else:
                survivors.append(p)
                location_rank[p.id] = profile.location_rank(r.location_key)
        session.commit()

        # Hydrate the postings most likely to pay off first: ones that have never failed
        # a fetch, then by enabled-location order, then newest first. Location comes before
        # age because a source can insert one country's rows after another's (Adzuna: CA
        # queries, then GB), and "newest first" alone then spends the whole cap on the
        # second country. A host that rate-limits us mid-run then only costs its own
        # postings, and the survivors behind it still get their pass-1 result.
        survivors.sort(
            key=lambda p: (
                1 if (p.prefilter_result or {}).get("hydrate_error") else 0,
                location_rank.get(p.id, len(profile.locations)),
                -p.first_seen_at.timestamp(),
            )
        )
        stopped_hosts: set[str] = set()
        budget_spent = False
        hydrate_attempts = 0
        last_hydrate_host: str | None = None
        max_hydrate = profile.scoring.max_hydrate_per_run
        for p in survivors:
            if not p.description_complete:
                host = urlparse(p.apply_url).netloc
                if budget_spent or host in stopped_hosts:
                    stats["hydrate_deferred"] += 1
                elif hydrate_attempts >= max_hydrate:
                    stats["hydrate_capped"] += 1
                else:
                    if last_hydrate_host is not None and host == last_hydrate_host:
                        _sleep(profile.scoring.hydrate_delay_s)
                    last_hydrate_host = host
                    hydrate_attempts += 1
                    release_snapshot(session)  # same reason as before source.fetch
                    try:
                        if hydrate_posting(session, p, client):
                            stats["hydrated"] += 1
                    except RateLimited as exc:
                        # One host throttling us says nothing about the others: skip only
                        # this host's remaining postings.
                        stopped_hosts.add(host)
                        stats["errors"][f"hydrate:{host}"] = repr(exc)[:200]
                        log.warning("hydration stopped for %s this run: %s", host, exc)
                    except BudgetExceeded as exc:
                        # The client's call budget is global — nothing more can be fetched.
                        budget_spent = True
                        stats["errors"]["hydrate"] = repr(exc)[:200]
                        log.warning("hydration stopped for this run: %s", exc)
            if p.description_complete:
                r = prefilter(p, profile, skill_terms)
                p.prefilter_result = {**(p.prefilter_result or {}), **r.as_dict(),
                                      "stage": "pass2"}
                if not r.passed:
                    p.status = "prefiltered_out"
            else:
                # Still snippet-only this run (capped, deferred, or hydrate_posting didn't
                # complete it). Keep status="new" and record stage="pass1" — the marker the
                # `fresh` query above uses to retry it next run, and what tells the scorer
                # this posting is not ready yet. "overlap" is dropped because a snippet
                # overlap count would read like a real one.
                r = prefilter(p, profile, skill_terms, check_overlap=False)
                partial = r.as_dict()
                partial.pop("overlap", None)
                p.prefilter_result = {**(p.prefilter_result or {}), **partial,
                                      "stage": "pass1"}
            reasons[r.reason] += 1
        session.commit()
        stats["prefilter"] = dict(reasons)

        if not no_llm and scorer is not None:
            stats["scoring"] = scorer(session)
        if not no_llm and contacts is not None:
            stats["contacts"] = contacts(session)

        run.finished_at = utcnow().replace(tzinfo=None)
        run.status = "ok"
        run.stats = stats
        session.commit()
        return run
    except Exception as exc:
        session.rollback()
        run = session.get(Run, run_id)
        run.status = "error"
        run.stats = {**stats, "fatal": repr(exc)[:500]}
        run.finished_at = utcnow().replace(tzinfo=None)
        session.commit()
        raise
