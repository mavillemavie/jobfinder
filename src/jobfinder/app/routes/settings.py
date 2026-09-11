from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobfinder import paths
from jobfinder.app import background
from jobfinder.app.deps import current_profile, current_settings, db_session, get_llm_dep, templates
from jobfinder.app.digest import send_digest
from jobfinder.config import Profile, SourceConfig, TitleCluster, save_profile
from jobfinder.db.models import AdapterHealth, SpendLedger
from jobfinder.seeding import SeedRejected, gap_report, master_diff, propose_seed, seed_guard
from jobfinder.settings import Settings
from jobfinder.tailoring.master_schema import (
    MasterResume,
    cover_letter_path,
    load_master,
    master_exists,
    master_resume_path,
)

router = APIRouter(prefix="/settings")
SENIORITIES = ["junior", "intermediate", "senior", "lead", "management", "intern", "unspecified"]
SOURCE_NAMES = [
    "adzuna", "reed", "jooble", "jsearch", "jobbank_rss", "remotive", "weworkremotely",
    "linkedin_guest", "linkedin_jobs", "active_jobs_db", "ats_boards",
]
KEYS_BY_SOURCE = {
    "linkedin_jobs": ("rapidapi_key",),
    "active_jobs_db": ("rapidapi_key",),
    "adzuna": ("adzuna_app_id", "adzuna_app_key"), "reed": ("reed_api_key",),
    "jooble": ("jooble_api_key",), "jsearch": ("rapidapi_key",),
}


def month_spend(session: Session) -> float:
    start = datetime.now(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    total = session.scalar(
        select(func.coalesce(func.sum(SpendLedger.usd_estimate), 0.0)).where(
            SpendLedger.occurred_at >= start
        )
    )
    return float(total or 0.0)


def _titles_text(profile: Profile) -> str:
    return "\n".join(f"{c.name}:\n" + "\n".join(c.include) for c in profile.titles.clusters)


def _parse_titles(text: str) -> list[TitleCluster]:
    clusters: list[TitleCluster] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith(":"):
            clusters.append(TitleCluster(name=line[:-1].strip(), include=[]))
        elif clusters:
            clusters[-1].include.append(line.lower())
        else:
            clusters.append(TitleCluster(name="default", include=[line.lower()]))
    return [c for c in clusters if c.include]


@router.get("")
def page(
    request: Request,
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
    settings: Settings = Depends(current_settings),
    flash: str | None = None,
):  # noqa: ANN201
    health = {h.adapter: h for h in session.scalars(select(AdapterHealth))}
    master_text = master_resume_path().read_text() if master_resume_path().exists() else ""
    cover_text = cover_letter_path().read_text() if cover_letter_path().exists() else ""
    keys = {name: settings.has(*KEYS_BY_SOURCE.get(name, ())) for name in SOURCE_NAMES}
    gaps: list[dict] = []
    if master_exists():
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=14)
        gaps = gap_report(session, load_master(), since=since, top=15)
    return templates.TemplateResponse(request, "settings.html", {
        "active": "settings", "profile": profile, "titles_text": _titles_text(profile),
        "seniorities": SENIORITIES, "source_names": SOURCE_NAMES, "keys": keys, "health": health,
        "spend": month_spend(session), "master_text": master_text, "cover_text": cover_text,
        "flash": flash,
        "contact_keys": {
            "apollo": bool(settings.apollo_api_key), "hunter": bool(settings.hunter_api_key),
            "serper": bool(settings.serper_api_key),
        },
        "digest_ready": settings.has("gmail_user", "gmail_app_password", "digest_to"),
        "gaps": gaps,
    })


@router.post("/profile")
async def save(request: Request, profile: Profile = Depends(current_profile)):  # noqa: ANN201
    form = await request.form()
    sc = profile.scoring
    profile.titles.clusters = _parse_titles(str(form.get("titles_include", "")))
    profile.titles.exclude_words = [
        w.strip() for w in str(form.get("exclude_words", "")).splitlines() if w.strip()
    ]
    profile.titles.search_keywords = [
        w.strip().lower() for w in str(form.get("search_keywords", "")).splitlines() if w.strip()
    ]
    profile.titles.seniority_allowed = [
        s for s in form.getlist("seniority_allowed") if s in SENIORITIES
    ] or ["unspecified"]
    for loc in profile.locations:
        loc.enabled = form.get(f"loc_{loc.key}") == "on"
    sc.match_threshold = int(form.get("match_threshold", sc.match_threshold))
    sc.max_llm_scored_per_run = int(form.get("max_llm_scored_per_run", sc.max_llm_scored_per_run))
    sc.max_posting_age_days = int(form.get("max_posting_age_days", sc.max_posting_age_days))
    sc.min_keyword_overlap = int(form.get("min_keyword_overlap", sc.min_keyword_overlap))
    sc.candidate_note = str(form.get("candidate_note", sc.candidate_note)).strip()
    for name in SOURCE_NAMES:
        cfg = profile.sources.setdefault(name, SourceConfig())
        cfg.enabled = form.get(f"src_{name}") == "on"
    profile.contacts.monthly_usd_cap = float(
        form.get("monthly_usd_cap", profile.contacts.monthly_usd_cap)
    )
    profile.scan.run_at = str(form.get("scan_run_at", profile.scan.run_at))
    profile.digest.send_at = str(form.get("digest_send_at", profile.digest.send_at))
    profile.digest.enabled = form.get("digest_enabled") == "on"
    profile.dashboard.public_base_url = str(
        form.get("public_base_url", profile.dashboard.public_base_url)
    )
    save_profile(profile)
    return RedirectResponse("/settings?flash=profile+saved", status_code=303)


@router.post("/master")
async def save_master(request: Request):  # noqa: ANN201
    form = await request.form()
    text = str(form.get("resume_yaml", ""))
    try:
        from ruamel.yaml import YAML

        master = MasterResume.model_validate(YAML(typ="safe").load(text) or {})
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            f"/settings?flash=invalid+master+yaml:+{str(exc)[:80]}", status_code=303
        )
    paths.ensure_dirs()
    master.to_yaml(master_resume_path())
    cover = str(form.get("cover_letter", "")).strip() + "\n"
    cover_letter_path().write_text(cover, encoding="utf-8")
    return RedirectResponse("/settings?flash=master+saved", status_code=303)


@router.post("/scan")
def scan_now(request: Request):  # noqa: ANN201
    background.start_scan_thread(getattr(request.app.state, "llm", None))
    return RedirectResponse("/runs", status_code=303)


@router.post("/digest")
def digest_now(
    session: Session = Depends(db_session),
    profile: Profile = Depends(current_profile),
    settings: Settings = Depends(current_settings),
):  # noqa: ANN201
    run = send_digest(session, settings=settings, profile=profile)
    detail = "sent" if run.status == "ok" else (run.stats or {}).get("error", "failed")[:100]
    return RedirectResponse(
        f"/settings?flash=digest+{run.status}:+{quote(detail)}", status_code=303
    )


def _write_master(new: MasterResume) -> str:
    import shutil

    path = master_resume_path()
    backup = path.with_name(f"resume.yaml.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy(path, backup)
    new.to_yaml(path)
    return backup.name


@router.post("/seed")
async def seed_propose(request: Request):  # noqa: ANN201
    """Ask the model to weave the ticked terms into the master; show the diff for review."""
    form = await request.form()
    terms = [t.strip() for t in form.getlist("terms") if t.strip()]
    custom = str(form.get("custom", ""))
    terms += [t.strip() for t in custom.split(",") if t.strip()]
    notes = str(form.get("notes", ""))
    if not terms:
        return RedirectResponse("/settings?flash=pick+at+least+one+term+to+seed", status_code=303)
    if not master_exists():
        return RedirectResponse("/settings?flash=no+master+resume+yet", status_code=303)
    master = load_master()
    try:
        new, changes = propose_seed(master, terms, get_llm_dep(request), notes=notes)
    except SeedRejected as exc:
        return RedirectResponse(
            f"/settings?flash=seed+rejected:+{quote(str(exc)[:200])}", status_code=303
        )
    from io import StringIO

    from ruamel.yaml import YAML

    buf = StringIO()
    YAML().dump(new.model_dump(mode="json"), buf)
    return templates.TemplateResponse(request, "seed_review.html", {
        "active": "settings", "terms": terms, "notes": notes, "diff": master_diff(master, new),
        "changes": changes, "proposed_yaml": buf.getvalue(),
    })


@router.post("/seed/apply")
async def seed_apply(request: Request):  # noqa: ANN201
    """Re-check the reviewed YAML against the current master, then write it with a backup."""
    form = await request.form()
    terms = [t.strip() for t in form.getlist("terms") if t.strip()]
    try:
        from ruamel.yaml import YAML

        new = MasterResume.model_validate(YAML(typ="safe").load(str(form.get("proposed_yaml", ""))))
    except Exception as exc:  # noqa: BLE001 — user-supplied YAML
        return RedirectResponse(
            f"/settings?flash=seed+rejected:+invalid+yaml:+{quote(str(exc)[:120])}", status_code=303
        )
    master = load_master()
    new.contact = master.contact
    reasons = seed_guard(master, new, terms)
    if reasons:
        return RedirectResponse(
            f"/settings?flash=seed+rejected:+{quote('; '.join(reasons)[:200])}", status_code=303
        )
    backup = _write_master(new)
    return RedirectResponse(f"/settings?flash=master+seeded+(backup+{backup})", status_code=303)
