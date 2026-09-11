from datetime import UTC, datetime, timedelta

from jobfinder.db.models import AdapterHealth, utcnow
from jobfinder.discovery.health import AdapterGuard, today_in


def test_daily_cap_and_reset(db_session) -> None:
    g = AdapterGuard(db_session, "adzuna", daily_cap=2)
    assert g.allowed() == (True, "ok") and g.remaining() == 2
    g.add_calls(2)
    assert g.allowed()[0] is False and "daily cap" in g.allowed()[1]
    row = db_session.get(AdapterHealth, "adzuna")
    row.calls_date = "2000-01-01"
    db_session.commit()
    assert g.allowed() == (True, "ok") and row.calls_today == 0


def test_cooldown_after_error(db_session) -> None:
    g = AdapterGuard(db_session, "linkedin_guest")
    g.record_error("429", cooldown_s=3600)
    ok, why = g.allowed()
    assert ok is False and why.startswith("cooldown until")
    row = db_session.get(AdapterHealth, "linkedin_guest")
    row.cooldown_until = utcnow().replace(tzinfo=None) - timedelta(seconds=1)
    db_session.commit()
    assert g.allowed()[0] is True
    g.record_ok()
    assert row.last_error is None and row.last_ok_at is not None


def test_today_in_uses_product_timezone() -> None:
    assert today_in("America/Montreal", datetime(2026, 9, 4, 3, 0, tzinfo=UTC)) == "2026-09-03"
    assert today_in("UTC", datetime(2026, 9, 4, 3, 0, tzinfo=UTC)) == "2026-09-04"


def test_budget_adapter_pools_the_cap_but_keeps_health_per_source(db_session) -> None:
    """ats_* adapters share one daily budget row while each keeps its own cooldown."""
    a = AdapterGuard(db_session, "ats_a", daily_cap=1, budget_adapter="ats_boards")
    b = AdapterGuard(db_session, "ats_b", daily_cap=1, budget_adapter="ats_boards")
    a.add_calls(1)
    assert db_session.get(AdapterHealth, "ats_boards").calls_today == 1
    assert a.row().calls_today == 0  # the per-source row is untouched by the pooled counter
    # the pooled counter gates every member of the pool
    assert b.remaining() == 0
    ok, why = b.allowed()
    assert ok is False and "daily cap" in why

    # …but a cooldown on one member never benches another
    a.record_error("429", cooldown_s=3600)
    assert a.allowed()[1].startswith("cooldown until")
    assert b.row().cooldown_until is None
