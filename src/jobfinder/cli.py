from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(help="jobfinder — JF's job-search machine", no_args_is_help=True)
console = Console()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """jobfinder — JF's job-search machine"""
    if ctx.invoked_subcommand is None:
        raise typer.Exit()


@app.command()
def version() -> None:
    """Print the installed version."""
    from jobfinder import __version__

    console.print(f"jobfinder {__version__}")


db_app = typer.Typer(help="Database commands")
app.add_typer(db_app, name="db")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply Alembic migrations to the configured database."""
    from jobfinder.db.migrate import upgrade_head

    upgrade_head()
    console.print("database up to date")


@app.command()
def ingest(
    provider: str | None = typer.Option(None, help="LLM provider override"),
    resume: str | None = typer.Option(
        None, help="Explicit resume .docx path (overrides auto-detect)"
    ),
    cover: str | None = typer.Option(
        None, help="Explicit cover letter .docx path (overrides auto-detect)"
    ),
) -> None:
    """Parse input/*.docx into master/resume.yaml and master/cover-letter.md."""
    from pathlib import Path

    from jobfinder import paths
    from jobfinder.llm import get_llm
    from jobfinder.settings import get_settings
    from jobfinder.tailoring.ingest import run_ingest

    paths.ensure_dirs()
    try:
        result = run_ingest(
            llm=get_llm(get_settings(), provider=provider),
            resume_file=Path(resume) if resume else None,
            cover_file=Path(cover) if cover else None,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red] — drop your resume and cover letter .docx into input/")
        raise typer.Exit(code=1) from exc
    console.print(f"resume  → {result.resume_path}")
    console.print(f"cover   → {result.cover_path or '(none)'}")
    for w in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")


@app.command()
def scan(no_llm: bool = typer.Option(False, "--no-llm", help="Discover + prefilter only")) -> None:
    """Run discovery → dedupe → prefilter → LLM fit scoring."""
    from jobfinder.logging_setup import configure_logging

    configure_logging()
    from jobfinder import paths
    from jobfinder.config import load_profile
    from jobfinder.contacts.waterfall import run_for_new_matches
    from jobfinder.db.session import session_scope
    from jobfinder.discovery.scan import run_scan
    from jobfinder.llm import get_llm
    from jobfinder.scoring.scorer import score_new_postings
    from jobfinder.settings import get_settings

    paths.ensure_dirs()
    settings = get_settings()
    profile = load_profile()
    with session_scope() as session:
        scorer = None if no_llm else (
            lambda s: score_new_postings(s, llm=get_llm(settings), profile=profile)
        )
        contacts_fn = None if (no_llm or not profile.contacts.auto_run_on_match) else (
            lambda s: run_for_new_matches(
                s, llm=get_llm(settings), settings=settings, profile=profile
            )
        )
        run = run_scan(
            session, profile=profile, settings=settings, no_llm=no_llm, scorer=scorer,
            contacts=contacts_fn,
        )
        st = run.stats
        console.print(f"scan finished: sources={st['sources']} upsert={st['upsert']}")
        console.print(f"prefilter={st['prefilter']} hydrated={st['hydrated']}")
        if "scoring" in st:
            console.print(f"scoring={st['scoring']}")
        if "contacts" in st:
            console.print(f"contacts={st['contacts']}")
        if st["skipped"]:
            console.print(f"[yellow]skipped:[/yellow] {st['skipped']}")
        if st["errors"]:
            console.print(f"[red]errors:[/red] {st['errors']}")


@app.command()
def rescore(posting_id: int) -> None:
    """Re-run LLM fit scoring for one posting (bypasses the cache)."""
    from jobfinder.config import load_profile
    from jobfinder.db.models import Posting
    from jobfinder.db.session import session_scope
    from jobfinder.llm import get_llm
    from jobfinder.scoring.scorer import score_posting
    from jobfinder.settings import get_settings
    from jobfinder.tailoring.master_schema import load_master

    llm = get_llm(get_settings())
    if hasattr(llm, "bypass_next"):
        llm.bypass_next()
    with session_scope() as session:
        posting = session.get(Posting, posting_id)
        if posting is None:
            console.print(f"[red]no posting {posting_id}[/red]")
            raise typer.Exit(code=1)
        score = score_posting(
            session, posting, master=load_master(), llm=llm, profile=load_profile()
        )
        console.print(
            f"{posting.title} @ {posting.company.name}: {score.fit_score} → {posting.status}"
        )
        for r in score.reasons:
            console.print(f"  - {r}")


@app.command()
def contacts(
    posting_id: int,
    show: bool = typer.Option(False, "--show", help="List stored contacts without running"),
) -> None:
    """Run contact discovery for one posting (or --show the stored rows)."""
    from jobfinder.config import load_profile
    from jobfinder.db.models import Posting
    from jobfinder.db.session import session_scope
    from jobfinder.llm import get_llm
    from jobfinder.settings import get_settings

    with session_scope() as session:
        posting = session.get(Posting, posting_id)
        if posting is None:
            console.print(f"[red]no posting {posting_id}[/red]")
            raise typer.Exit(code=1)
        if not show:
            from jobfinder.contacts import waterfall

            settings = get_settings()
            run = waterfall.run_for_posting(
                session, posting_id, llm=get_llm(settings), settings=settings,
                profile=load_profile(),
            )
            console.print(f"run {run.id}: status={run.status} credits={run.credits}")
            for s in run.steps:
                flag = "skip" if s.get("skipped") else ("ERR" if not s.get("ok", True) else "ok")
                detail = s.get("skipped") or s.get("error") or ""
                console.print(
                    f"  {flag:4s} {s['name']:17s} {s.get('credits') or ''} {detail}",
                    soft_wrap=True,
                )
        console.print(f"{posting.title} @ {posting.company.name}", soft_wrap=True)
        for c in sorted(posting.contacts, key=lambda c: -c.confidence):
            email = f"{c.email} ({c.email_status})" if c.email else "no email"
            phone = f"{c.phone} ({c.phone_kind})" if c.phone else "no phone"
            console.print(
                f"  [{c.confidence:.2f}] {c.full_name or '(no name)'} | {c.title or ''} | "
                f"{c.role_kind} | {email} | {phone}",
                soft_wrap=True,
            )
        manual = [c for c in posting.contacts if (c.evidence or {}).get("needs_manual")]
        if manual:
            console.print("[yellow]manual lookup needed[/yellow]")


@app.command()
def tailor(
    posting_id: int,
    lang: str | None = typer.Option(None, help="en | fr (default: follow posting)"),
) -> None:
    """Generate the tailored resume + cover letter (.docx + .pdf) for a posting."""
    from jobfinder.config import load_profile
    from jobfinder.db.session import session_scope
    from jobfinder.llm import get_llm
    from jobfinder.settings import get_settings
    from jobfinder.tailoring.tailor import tailor_posting

    with session_scope() as session:
        docs = tailor_posting(
            session, posting_id, llm=get_llm(get_settings()), profile=load_profile(), lang=lang
        )
        for d in docs:
            console.print(f"{d.kind:13} {d.format:4} {d.status:12} {d.path}", soft_wrap=True)
        rep = docs[0].ats_report
        missing = ", ".join(rep["keyword_missing"]) or "none"
        console.print(
            f"ATS score {docs[0].ats_score} | missing keywords: {missing}", soft_wrap=True
        )
        if not docs[0].truth_check["ok"]:
            console.print("[yellow]truth check findings:[/yellow]")
            for f in docs[0].truth_check["deterministic"] + docs[0].truth_check["llm"]:
                console.print(f"  - {f}", soft_wrap=True)


@app.command()
def documents(posting_id: int) -> None:
    """List generated documents for a posting."""
    from sqlalchemy import select

    from jobfinder.db.models import Document
    from jobfinder.db.session import session_scope

    with session_scope() as session:
        q = select(Document).where(Document.posting_id == posting_id)
        for d in session.scalars(q):
            console.print(
                f"{d.id:4} {d.kind:13} {d.format:4} {d.language} {d.status:12} "
                f"ats={d.ats_score} {d.path}",
                soft_wrap=True,
            )


@app.command()
def postings(
    status: str | None = typer.Option(
        None, help="new | prefiltered_out | scored | match | dismissed"
    ),
    limit: int = typer.Option(50),
) -> None:
    """List postings."""
    from rich.table import Table
    from sqlalchemy import select

    from jobfinder.db.models import Posting
    from jobfinder.db.session import session_scope

    with session_scope() as session:
        q = select(Posting).order_by(Posting.first_seen_at.desc()).limit(limit)
        if status:
            q = q.where(Posting.status == status)
        table = Table("id", "status", "stage", "title", "company", "where", "seen", "reason")
        for p in session.scalars(q):
            pr = p.prefilter_result or {}
            # "pass1" postings are snippet-only and not scoreable yet; say so plainly.
            stage = pr.get("stage", "") if p.description_complete else "pending-hydration"
            table.add_row(
                str(p.id),
                p.status,
                stage,
                p.title[:40],
                p.company.name[:24],
                f"{p.city or ''} {p.country or ''} {p.remote_type}",
                p.first_seen_at.strftime("%m-%d"),
                pr.get("reason", ""),
            )
        console.print(table)


@app.command()
def serve(
    port: int | None = typer.Option(None, help="default: profile.dashboard.port"),
    host: str | None = typer.Option(None, help="default: profile.dashboard.bind_host"),
) -> None:
    """Run the dashboard + the in-process scheduler (scan and digest cron jobs)."""
    from jobfinder.logging_setup import configure_logging

    configure_logging()
    import uvicorn

    from jobfinder.app.main import create_app
    from jobfinder.config import load_profile

    prof = load_profile()
    uvicorn.run(
        create_app(), host=host or prof.dashboard.bind_host, port=port or prof.dashboard.port,
        log_level="info",
    )


@app.command()
def digest(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the text digest instead of sending"
    ),
) -> None:
    """Send (or preview) the daily digest email."""
    from jobfinder.logging_setup import configure_logging

    configure_logging()
    from jobfinder.app.digest import build_digest, render_digest, send_digest
    from jobfinder.config import load_profile
    from jobfinder.db.session import session_scope
    from jobfinder.settings import get_settings

    with session_scope() as session:
        if dry_run:
            _, _, text = render_digest(build_digest(session, profile=load_profile()))
            console.print(text, soft_wrap=True)
            return
        run = send_digest(session, settings=get_settings(), profile=load_profile())
        console.print(f"digest {run.status}: {run.stats}", soft_wrap=True)
        if run.status != "ok":
            raise typer.Exit(code=1)


@app.command()
def doctor() -> None:
    """Check database, master files, LLM CLI, keys, SMTP settings, and port."""
    import shutil
    import socket

    from sqlalchemy import text

    from jobfinder import paths
    from jobfinder.config import load_profile
    from jobfinder.db.session import get_engine
    from jobfinder.settings import get_settings
    from jobfinder.tailoring.master_schema import cover_letter_path, master_exists

    s, prof = get_settings(), load_profile()
    rows: list[tuple[str, bool, str]] = []
    try:
        with get_engine().connect() as c:
            c.execute(text("select 1"))
        rows.append(("database", True, str(paths.db_path())))
    except Exception as exc:  # noqa: BLE001 — report, don't crash the doctor
        rows.append(("database", False, str(exc)[:80]))
    rows.append(("master resume", master_exists(), str(paths.master_dir() / "resume.yaml")))
    rows.append(("master cover letter", cover_letter_path().exists(), str(cover_letter_path())))
    claude_ok = shutil.which("claude") is not None or s.llm_provider != "claude_code"
    rows.append(("claude CLI", claude_ok, f"provider={s.llm_provider}"))
    keys = {
        "adzuna": ("adzuna_app_id", "adzuna_app_key"), "reed": ("reed_api_key",),
        "jooble": ("jooble_api_key",), "jsearch": ("rapidapi_key",), "apollo": ("apollo_api_key",),
        "hunter": ("hunter_api_key",), "serper": ("serper_api_key",),
    }
    for name, fields in keys.items():
        present = s.has(*fields)
        rows.append((f"key: {name}", present, "set" if present else "missing (docs/SETUP-KEYS.md)"))
    gmail = s.has("gmail_user", "gmail_app_password", "digest_to")
    rows.append(("gmail digest", gmail, "GMAIL_USER / GMAIL_APP_PASSWORD / DIGEST_TO"))
    with socket.socket() as sock:
        free = sock.connect_ex(("127.0.0.1", prof.dashboard.port)) != 0
    port_detail = "free" if free else "in use (dashboard running?)"
    rows.append((f"port {prof.dashboard.port}", True, port_detail))
    for name, ok, detail in rows:
        flag = "[green]ok     [/green]" if ok else "[yellow]missing[/yellow]"
        console.print(f"{flag} {name:22s} {detail}", soft_wrap=True)
    if not all(ok for name, ok, _ in rows if name == "database"):
        raise typer.Exit(code=1)


@app.command()
def stats(days: int = typer.Option(14, help="Window in days (the trial is 14)")) -> None:
    """Measurement summary for the free-tier trial and the playbook's day-14 decision rule."""
    from datetime import UTC, datetime, timedelta

    from jobfinder.db.session import session_scope
    from jobfinder.stats import format_summary, measurement_summary

    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    with session_scope() as session:
        for line in format_summary(measurement_summary(session, since=since)):
            console.print(line, soft_wrap=True)


@app.command()
def gaps(
    days: int = typer.Option(14, help="Look back this many days"),
    top: int = typer.Option(25, help="How many terms to show"),
) -> None:
    """Vocabulary postings keep asking for that the master résumé does not say (seed candidates)."""
    from datetime import UTC, datetime, timedelta

    from jobfinder.db.session import session_scope
    from jobfinder.seeding import gap_report
    from jobfinder.tailoring.master_schema import load_master, master_exists

    if not master_exists():
        console.print("[red]no master resume[/red] — run `jobfinder ingest` first")
        raise typer.Exit(code=1)
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    with session_scope() as session:
        rows = gap_report(session, load_master(), since=since, top=top)
    if not rows:
        console.print("no gaps recorded yet — they appear after scans score and tailor postings")
        return
    console.print(f"{'term':32s} {'count':>5s}  scores/documents/drops   (last {days} days)")
    for r in rows:
        console.print(
            f"{r['term']:32s} {r['count']:5d}  {r['scores']}/{r['documents']}/{r['drops']}",
            soft_wrap=True,
        )
    console.print("confirm the true ones with: jobfinder seed --term \"<term>\" [--term ...]")


@app.command()
def seed(
    term: list[str] = typer.Option(
        ..., "--term", help="A skill or keyword that is TRUE for you (repeat for several)"
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Write master/resume.yaml (a timestamped .bak copy is kept)"
    ),
    note: str = typer.Option(
        "", "--note", help='Nuance for the model, e.g. "Python: working knowledge, not primary"'
    ),
) -> None:
    """Weave confirmed terms into the master résumé; shows the diff, writes only with --apply."""
    import shutil
    from datetime import datetime

    from jobfinder.llm import get_llm
    from jobfinder.seeding import SeedRejected, master_diff, propose_seed
    from jobfinder.settings import get_settings
    from jobfinder.tailoring.master_schema import load_master, master_resume_path

    master = load_master()
    try:
        new, changes = propose_seed(master, term, get_llm(get_settings()), notes=note)
    except SeedRejected as exc:
        console.print(f"[red]rejected:[/red] {exc}", soft_wrap=True)
        raise typer.Exit(code=1) from exc
    console.print(master_diff(master, new), soft_wrap=True)
    for c in changes:
        console.print(f"  - {c}", soft_wrap=True)
    if not apply:
        console.print("not applied — re-run with --apply to write master/resume.yaml")
        return
    path = master_resume_path()
    backup = path.with_name(f"resume.yaml.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy(path, backup)
    new.to_yaml(path)
    console.print(f"applied → {path} (backup {backup.name})", soft_wrap=True)
