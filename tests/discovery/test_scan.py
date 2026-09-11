from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy import select
from typer.testing import CliRunner

from jobfinder import paths
from jobfinder.cli import app
from jobfinder.config import load_profile
from jobfinder.db.models import AdapterHealth, Posting, Run
from jobfinder.discovery import scan
from jobfinder.discovery.base import RawPosting, SourceError
from jobfinder.discovery.dedupe import upsert_raw_postings
from jobfinder.discovery.fetch import HttpClient, RateLimited
from jobfinder.discovery.health import AdapterGuard
from jobfinder.discovery.registry import build_sources
from jobfinder.discovery.scan import ready_to_score, run_scan
from jobfinder.settings import Settings


class GoodSource:
    name = "good"

    def fetch(self, profile, since):
        return [
            RawPosting(
                "good", "1", "https://g.test/1", "Data Analyst", "Acme",
                location_raw="Montreal, QC",
                description="SQL Power BI Python Tableau reporting",
                posted_at=datetime.now(UTC),
            ),
            RawPosting(
                "good", "2", "https://g.test/2", "Chef de cuisine", "Bistro",
                location_raw="Montreal, QC",
                description="cooking",
            ),
        ]


class LimitedSource:
    name = "limited"

    def fetch(self, profile, since):
        raise RateLimited("https://l.test", 900)


class BrokenSource:
    name = "broken"

    def fetch(self, profile, since):
        raise SourceError("boom")


def test_run_scan_isolates_failures_and_prefilters(home, db_session, monkeypatch) -> None:
    (paths.master_dir() / "resume.yaml").write_text(
        (paths.repo_root() / "tests" / "fixtures" / "master" / "resume.yaml").read_text()
    )
    profile = load_profile()
    run = run_scan(db_session, profile=profile, settings=Settings(_env_file=None), no_llm=True,
                   client=HttpClient(), sources=[GoodSource(), LimitedSource(), BrokenSource()])
    assert run.status == "ok"
    assert run.stats["upsert"]["good"]["new"] == 2
    assert run.stats["errors"]["limited"].startswith("429")
    assert "boom" in run.stats["errors"]["broken"]
    assert db_session.get(AdapterHealth, "limited").cooldown_until is not None
    statuses = {p.title: p.status for p in db_session.scalars(select(Posting))}
    assert statuses == {"Data Analyst": "new", "Chef de cuisine": "prefiltered_out"}
    ok = db_session.scalar(select(Posting).where(Posting.title == "Data Analyst"))
    assert ok.prefilter_result["passed"] is True and ok.prefilter_result["overlap"] >= 3
    assert run.stats["prefilter"] == {"ok": 1, "wrong_title": 1}


def test_scorer_hook_called_unless_no_llm(home, db_session) -> None:
    called = []
    run_scan(db_session, profile=load_profile(), settings=Settings(_env_file=None), no_llm=False,
             client=HttpClient(), sources=[], scorer=lambda s: called.append(1) or {"scored": 0})
    assert called == [1]
    run_scan(db_session, profile=load_profile(), settings=Settings(_env_file=None), no_llm=True,
             client=HttpClient(), sources=[], scorer=lambda s: called.append(2))
    assert called == [1]


def test_build_sources_skips_missing_keys_and_disabled(home) -> None:
    profile = load_profile()
    profile.sources["remotive"].enabled = False
    sources, skipped = build_sources(
        profile, Settings(_env_file=None), HttpClient(), lambda ats: []
    )
    names = {s.name for s in sources}
    assert {"jobbank_rss", "weworkremotely", "linkedin_guest", "ats_greenhouse"} <= names
    assert "adzuna" in skipped and "missing" in skipped["adzuna"]
    assert skipped["remotive"] == "disabled"
    assert skipped["linkedin_jobs"] == "missing RAPIDAPI_KEY"
    assert skipped["active_jobs_db"] == "missing RAPIDAPI_KEY"


def test_cli_scan_no_llm_and_postings(home, db_session, monkeypatch) -> None:
    monkeypatch.setattr("jobfinder.discovery.scan.build_sources", lambda *a, **k: ([], {}))
    runner = CliRunner()
    result = runner.invoke(app, ["scan", "--no-llm"])
    assert result.exit_code == 0 and "scan finished" in result.stdout
    assert db_session.scalar(select(Run)) is not None
    result = runner.invoke(app, ["postings", "--status", "new"])
    assert result.exit_code == 0


def test_fatal_error_marks_run_error(home, db_session) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        run_scan(
            db_session, profile=load_profile(), settings=Settings(_env_file=None),
            no_llm=False, client=HttpClient(), sources=[],
            scorer=lambda s: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    run = db_session.scalar(select(Run))
    assert run.status == "error"
    assert run.finished_at is not None
    assert "boom" in run.stats["fatal"]


class SnippetSource:
    """Three same-host, snippet-only postings — needs hydration before prefilter pass 2."""

    name = "snippet"

    def fetch(self, profile, since):
        return [
            RawPosting(
                "snippet", str(i), f"https://boards.example.test/job/{i}",
                "Data Analyst", f"Acme{i}",
                location_raw="Montreal, QC",
                description="short snippet",
                description_complete=False,
            )
            for i in range(3)
        ]


def test_hydration_cap_and_delay(home, db_session, monkeypatch) -> None:
    profile = load_profile()
    profile.scoring.max_hydrate_per_run = 2
    profile.scoring.hydrate_delay_s = 0.5
    sleeps: list[float] = []
    monkeypatch.setattr(scan, "_sleep", sleeps.append)
    monkeypatch.setattr(scan, "hydrate_posting", lambda session, posting, client: True)
    run = run_scan(
        db_session, profile=profile, settings=Settings(_env_file=None), no_llm=True,
        client=HttpClient(), sources=[SnippetSource()],
    )
    assert run.stats["hydrated"] == 2
    assert run.stats["hydrate_capped"] == 1
    assert sleeps == [0.5]


class FakeAtsSource:
    def __init__(self, name: str, client: HttpClient, url: str) -> None:
        self.name = name
        self._client = client
        self._url = url

    def fetch(self, profile, since):
        self._client.get(self._url)
        return []


@respx.mock
def test_ats_boards_cap_is_pooled_but_cooldown_is_per_source(home, db_session) -> None:
    respx.get("https://ats-a.test/jobs").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://ats-b.test/jobs").mock(return_value=httpx.Response(200, json=[]))
    profile = load_profile()
    profile.sources["ats_boards"].daily_calls = 1
    client = HttpClient()
    src_a = FakeAtsSource("ats_a", client, "https://ats-a.test/jobs")
    src_b = FakeAtsSource("ats_b", client, "https://ats-b.test/jobs")
    run = run_scan(
        db_session, profile=profile, settings=Settings(_env_file=None), no_llm=True,
        client=client, sources=[src_a, src_b],
    )
    assert run.stats["sources"]["ats_a"] == 0
    assert "ats_b" not in run.stats["sources"]
    assert "daily cap" in run.stats["skipped"]["ats_b"]
    assert db_session.get(AdapterHealth, "ats_boards").calls_today == 1
    # health rows are per source, so ats_a's success is recorded against ats_a
    assert db_session.get(AdapterHealth, "ats_a").last_ok_at is not None

    # Budget is roomy again but ats_a is cooling down — that must not skip ats_b, which
    # is what the shared cap/cooldown key used to do.
    profile.sources["ats_boards"].daily_calls = 10
    AdapterGuard(db_session, "ats_a").record_error("429 from ats-a", cooldown_s=3600)
    run2 = run_scan(
        db_session, profile=profile, settings=Settings(_env_file=None), no_llm=True,
        client=client, sources=[src_a, src_b],
    )
    assert "cooldown" in run2.stats["skipped"]["ats_a"]
    assert run2.stats["sources"]["ats_b"] == 0
    assert "ats_b" not in run2.stats["skipped"]


class OnePostingSource:
    """Single good posting for testing upsert failure isolation."""

    name = "good"

    def fetch(self, profile, since):
        return [
            RawPosting(
                "good", "1", "https://g.test/1", "Data Analyst", "Acme",
                location_raw="Montreal, QC",
                description="SQL Power BI Python Tableau reporting",
                posted_at=datetime.now(UTC),
            ),
        ]


def test_upsert_commit_failure_isolated_and_run_still_ok(home, db_session, monkeypatch) -> None:
    """When upsert_raw_postings fails with a commit error, session is rolled back
    and the run still completes with status='ok' and the error recorded in stats."""
    profile = load_profile()

    def mock_upsert_fail(*args, **kwargs):
        raise RuntimeError("commit boom")

    monkeypatch.setattr(scan, "upsert_raw_postings", mock_upsert_fail)
    run = run_scan(
        db_session, profile=profile, settings=Settings(_env_file=None), no_llm=True,
        client=HttpClient(), sources=[OnePostingSource()],
    )
    assert run.status == "ok"
    assert "commit boom" in run.stats["errors"]["good"]
    # Verify session is usable and Run row is accessible
    fetched_run = db_session.scalar(select(Run))
    assert fetched_run is not None
    assert fetched_run.status == "ok"


class TwoHostSnippetSource:
    """Two snippet-only postings on each of two hosts."""

    name = "twohost"

    def fetch(self, profile, since):
        return [
            RawPosting(
                "twohost", f"{host}-{i}", f"https://{host}.test/job/{i}",
                "Data Analyst", f"Acme {host} {i}",
                location_raw="Montreal, QC",
                description="short snippet",
                description_complete=False,
            )
            for i in range(2)
            for host in ("hosta", "hostb")
        ]


def test_rate_limit_stops_only_the_offending_host(home, db_session, monkeypatch) -> None:
    profile = load_profile()
    profile.scoring.hydrate_delay_s = 0
    monkeypatch.setattr(scan, "_sleep", lambda _s: None)
    seen: list[str] = []

    def fake_hydrate(session, posting, client):
        seen.append(posting.apply_url)
        if "hosta.test" in posting.apply_url:
            raise RateLimited(posting.apply_url, 60)
        posting.description_complete = True
        posting.description_text = "SQL Power BI Python Tableau reporting " * 3
        return True

    monkeypatch.setattr(scan, "hydrate_posting", fake_hydrate)
    run = run_scan(
        db_session, profile=profile, settings=Settings(_env_file=None), no_llm=True,
        client=HttpClient(), sources=[TwoHostSnippetSource()],
    )
    # hosta 429s on its first posting and is skipped for the rest of the run; hostb is
    # untouched by that and hydrates both of its postings.
    assert len([u for u in seen if "hosta.test" in u]) == 1
    assert len([u for u in seen if "hostb.test" in u]) == 2
    assert run.stats["hydrated"] == 2
    assert run.stats["hydrate_deferred"] == 1
    assert run.stats["hydrate_capped"] == 0
    assert any(k.startswith("hydrate:hosta.test") for k in run.stats["errors"])


def test_survivors_with_a_hydrate_error_are_tried_last(home, db_session, monkeypatch) -> None:
    """Ordering: a posting that already failed a fetch must not crowd out fresh ones."""
    profile = load_profile()
    profile.scoring.max_hydrate_per_run = 1
    monkeypatch.setattr(scan, "_sleep", lambda _s: None)
    upsert_raw_postings(
        db_session,
        [
            RawPosting(
                "snippet", str(i), f"https://boards.example.test/job/{i}",
                "Data Analyst", f"Acme{i}", location_raw="Montreal, QC",
                description="short snippet", description_complete=False,
            )
            for i in range(3)
        ],
        max_age_days=14,
    )
    failed = db_session.scalars(select(Posting).order_by(Posting.id)).first()
    failed.prefilter_result = {"hydrate_error": "404", "hydrate_attempts": 1}
    db_session.commit()

    tried: list[int] = []
    monkeypatch.setattr(
        scan, "hydrate_posting",
        lambda session, posting, client: tried.append(posting.id) or False,
    )
    run = run_scan(
        db_session, profile=profile, settings=Settings(_env_file=None), no_llm=True,
        client=HttpClient(), sources=[],
    )
    assert len(tried) == 1 and failed.id not in tried
    assert run.stats["hydrate_capped"] == 2


def test_stage_marks_snippet_only_postings_and_gates_scoring(home, db_session, monkeypatch) -> None:
    profile = load_profile()
    profile.scoring.max_hydrate_per_run = 0
    run = run_scan(
        db_session, profile=profile, settings=Settings(_env_file=None), no_llm=True,
        client=HttpClient(), sources=[SnippetSource()],
    )
    assert run.stats["hydrate_capped"] == 3
    snippets = db_session.scalars(select(Posting)).all()
    assert {p.prefilter_result["stage"] for p in snippets} == {"pass1"}
    assert all(p.status == "new" for p in snippets)
    assert not any(ready_to_score(p) for p in snippets)

    # Next run the cap is gone: they are re-selected (stage != "pass2"), hydrated, and
    # become scoreable.
    profile.scoring.max_hydrate_per_run = 30
    monkeypatch.setattr(scan, "_sleep", lambda _s: None)

    def fake_hydrate(session, posting, client):
        posting.description_complete = True
        posting.description_text = "SQL Power BI Python Tableau reporting " * 3
        return True

    monkeypatch.setattr(scan, "hydrate_posting", fake_hydrate)
    run2 = run_scan(
        db_session, profile=profile, settings=Settings(_env_file=None), no_llm=True,
        client=HttpClient(), sources=[],
    )
    assert run2.stats["hydrated"] == 3
    done = db_session.scalars(select(Posting)).all()
    assert {p.prefilter_result["stage"] for p in done} == {"pass2"}
    assert all(ready_to_score(p) for p in done)


def test_hydration_follows_enabled_location_order(home, db_session, monkeypatch) -> None:
    """Newest-first starved Canada on day 1: Adzuna inserts UK rows after CA rows, so 99 UK
    pages were fetched and 1 Canadian. Enabled-location order (Canada before uk) wins first."""
    profile = load_profile()
    profile.scoring.max_hydrate_per_run = 1
    monkeypatch.setattr(scan, "_sleep", lambda _s: None)
    upsert_raw_postings(
        db_session,
        [RawPosting(
            "snippet", "ca", "https://boards.example.test/job/ca", "Data Analyst", "Acme",
            location_raw="Montreal, QC", description="short", description_complete=False,
        )],
        max_age_days=14,
    )
    upsert_raw_postings(  # a second batch, so the UK row is the newer one
        db_session,
        [RawPosting(
            "snippet", "uk", "https://boards.example.test/job/uk", "Data Analyst", "Bristol Co",
            location_raw="Bristol, UK", description="short", description_complete=False,
        )],
        max_age_days=14,
    )
    rows = {p.apply_url.rsplit("/", 1)[1]: p for p in db_session.scalars(select(Posting))}
    assert rows["uk"].first_seen_at >= rows["ca"].first_seen_at and rows["uk"].country == "GB"
    tried: list[int] = []
    monkeypatch.setattr(
        scan, "hydrate_posting", lambda session, posting, client: tried.append(posting.id) or False
    )
    run = run_scan(
        db_session, profile=profile, settings=Settings(_env_file=None), no_llm=True,
        client=HttpClient(), sources=[],
    )
    assert tried == [rows["ca"].id] and run.stats["hydrate_capped"] == 1


class CommittingSource(GoodSource):
    """Fetches fine, but another connection commits during the fetch — a Shortlist click
    or the digest landing while the scan is inside a multi-second HTTP call."""

    name = "committing"

    def fetch(self, profile, since):
        from sqlalchemy.orm import Session

        from jobfinder.db.session import get_engine

        other = Session(get_engine(), expire_on_commit=False)
        other.add(Run(kind="digest", status="ok"))
        other.commit()
        other.close()
        return super().fetch(profile, since)


def test_run_scan_survives_a_commit_from_another_connection_during_fetch(
    home, db_session
) -> None:
    run = run_scan(db_session, profile=load_profile(), settings=Settings(_env_file=None),
                   no_llm=True, client=HttpClient(), sources=[CommittingSource()])
    assert run.status == "ok"
    assert run.stats["upsert"]["committing"]["new"] == 2
