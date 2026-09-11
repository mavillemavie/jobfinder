from jobfinder.app.scheduler import build_scheduler
from jobfinder.config import load_profile


def test_jobs_are_cron_scheduled_in_montreal_time(home) -> None:
    profile = load_profile()
    profile.scan.run_at, profile.digest.send_at = "06:30", "07:00"
    sched = build_scheduler(profile)
    jobs = {j.id: j for j in sched.get_jobs()}
    assert set(jobs) == {"scan", "digest", "catchup"}
    scan = jobs["scan"].trigger
    fields = {f.name: str(f) for f in scan.fields}
    assert fields["hour"] == "6" and fields["minute"] == "30"
    assert str(scan.timezone) == "America/Montreal"
    dig = {f.name: str(f) for f in jobs["digest"].trigger.fields}
    assert dig["hour"] == "7" and dig["minute"] == "0"
    catch = {f.name: str(f) for f in jobs["catchup"].trigger.fields}
    assert catch["hour"] == "*" and catch["minute"] == "30"
    assert str(jobs["catchup"].trigger.timezone) == "America/Montreal"


def test_digest_job_absent_when_disabled(home) -> None:
    profile = load_profile()
    profile.digest.enabled = False
    assert [j.id for j in build_scheduler(profile).get_jobs()] == ["scan", "catchup"]


def test_send_digest_now_route(client, monkeypatch) -> None:
    import jobfinder.app.routes.settings as settings_route

    calls: list[dict] = []

    def fake_send(session, *, settings, profile):
        calls.append({"to": settings.digest_to})
        from jobfinder.db.models import Run

        return Run(kind="digest", status="error", stats={"error": "no gmail settings"})

    monkeypatch.setattr(settings_route, "send_digest", fake_send)
    r = client.post("/settings/digest", follow_redirects=False)
    assert r.status_code == 303 and "digest" in r.headers["location"]
    assert len(calls) == 1
    page = client.get(r.headers["location"])
    assert "no gmail settings" in page.text
