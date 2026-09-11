from __future__ import annotations

import json
from collections import Counter
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobfinder import paths
from jobfinder.db.models import Contact, ContactRun, Document, Posting, Run, SpendLedger


def _run_counts(runs: list[Run], kind: str) -> dict:
    rs = [r for r in runs if r.kind == kind]
    return {
        "total": len(rs),
        "ok": sum(1 for r in rs if r.status == "ok"),
        "error": sum(1 for r in rs if r.status == "error"),
    }


def _llm_calls_since(since: datetime) -> dict:
    path = paths.llm_cache_dir() / "calls.jsonl"
    if not path.exists():
        return {}
    counts: Counter[str] = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if datetime.utcfromtimestamp(rec.get("ts", 0)) >= since:
            counts[rec.get("task", "?")] += 1
    return dict(counts)


def measurement_summary(session: Session, *, since: datetime) -> dict:
    """The numbers the playbook's day-14 decision rule needs, for everything since `since`."""
    runs = session.scalars(select(Run).where(Run.started_at >= since)).all()
    postings = session.scalars(select(Posting).where(Posting.first_seen_at >= since)).all()
    by_status = Counter(p.status for p in postings)
    contact_runs = session.scalars(
        select(ContactRun).where(ContactRun.created_at >= since)
    ).all()
    run_posting_ids = {r.posting_id for r in contact_runs}
    contacts = {
        "matches_with_run": 0, "named_person": 0, "verified_email": 0, "any_phone": 0,
        "direct_phone": 0, "switchboard_only": 0, "needs_manual": 0,
    }
    blockers = {"no_named_person": 0, "no_email_found": 0}
    for pid in sorted(run_posting_ids):
        rows = session.scalars(select(Contact).where(Contact.posting_id == pid)).all()
        contacts["matches_with_run"] += 1
        named = any(c.full_name for c in rows)
        # a generic careers@/hr@ address is not the goal: verified means a *named* person
        verified = any(c.full_name and c.email and c.email_status == "verified" for c in rows)
        contacts["named_person"] += int(named)
        contacts["verified_email"] += int(verified)
        contacts["any_phone"] += int(any(c.phone for c in rows))
        contacts["direct_phone"] += int(any(c.phone_kind == "direct" for c in rows))
        contacts["switchboard_only"] += int(
            bool(rows) and all((c.evidence or {}).get("switchboard_only") for c in rows)
        )
        contacts["needs_manual"] += int(any((c.evidence or {}).get("needs_manual") for c in rows))
        if not verified:
            blockers["no_named_person" if not named else "no_email_found"] += 1
    docs = session.scalars(
        select(Document).where(Document.created_at >= since, Document.kind == "resume",
                               Document.format == "docx")
    ).all()
    credits: Counter[str] = Counter()
    for r in contact_runs:
        for provider, units in (r.credits or {}).items():
            credits[provider] += float(units)
    spend = session.scalar(
        select(func.coalesce(func.sum(SpendLedger.usd_estimate), 0.0)).where(
            SpendLedger.occurred_at >= since
        )
    )
    return {
        "since": since,
        "scans": _run_counts(runs, "scan"),
        "digests": _run_counts(runs, "digest"),
        "postings": {"total": len(postings), **dict(by_status)},
        "contacts": contacts,
        "contact_blockers": blockers,
        "documents": {
            "ready": sum(1 for d in docs if d.status == "ready"),
            "needs_review": sum(1 for d in docs if d.status == "needs_review"),
        },
        "credits": dict(credits),
        "spend_usd": round(float(spend or 0.0), 4),
        "llm_calls": _llm_calls_since(since),
    }


def format_summary(m: dict) -> list[str]:
    c, b = m["contacts"], m["contact_blockers"]
    p = m["postings"]
    lines = [
        f"since {m['since']:%Y-%m-%d %H:%M} UTC",
        f"scans: {m['scans']['total']} ({m['scans']['ok']} ok, {m['scans']['error']} error)"
        f" | digests: {m['digests']['total']} ({m['digests']['ok']} ok, "
        f"{m['digests']['error']} error)",
        f"postings: {p['total']} new | matches {p.get('match', 0)} | scored {p.get('scored', 0)} | "
        f"new {p.get('new', 0)} | prefiltered_out {p.get('prefiltered_out', 0)}",
        f"contacts on {c['matches_with_run']} matches: named person {c['named_person']} | "
        f"verified email {c['verified_email']} | any phone {c['any_phone']} | "
        f"direct phone {c['direct_phone']} | switchboard only {c['switchboard_only']} | "
        f"needs manual {c['needs_manual']}",
        f"day-14 blockers (no verified email): no_named_person {b['no_named_person']} → fund "
        f"Serper | no_email_found {b['no_email_found']} → fund Hunter",
        f"documents: ready {m['documents']['ready']} | needs_review "
        f"{m['documents']['needs_review']}",
        f"credits: {m['credits'] or {}} | spend ${m['spend_usd']:.2f} | "
        f"llm calls {m['llm_calls'] or {}}",
    ]
    return lines
