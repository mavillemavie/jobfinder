from datetime import timedelta

import pytest
from sqlalchemy import select

from jobfinder.config import load_profile
from jobfinder.contacts.budget import Budget, BudgetExhausted, month_start_utc
from jobfinder.db.models import SpendLedger


def test_per_job_cap(home, db_session, posting) -> None:
    b = Budget(db_session, load_profile(), posting_id=posting.id)
    assert b.can_spend("hunter", 0.5) == (True, "ok")
    b.spend("hunter", 1.0)
    b.spend("hunter", 0.5)
    b.spend("hunter", 0.5)
    ok, why = b.can_spend("hunter", 0.5)
    assert ok is False and "per-job cap" in why
    with pytest.raises(BudgetExhausted):
        b.spend("hunter", 0.5)
    rows = db_session.scalars(select(SpendLedger)).all()
    assert len(rows) == 3
    assert sum(r.credits for r in rows) == 2.0
    assert all(r.usd_estimate == 0 for r in rows)


def test_monthly_free_units_and_previous_month_ignored(home, db_session, posting) -> None:
    profile = load_profile()
    profile.contacts.provider_limits["serper"].monthly_units = 2
    db_session.add(
        SpendLedger(
            provider="serper", credits=5, usd_estimate=0,
            occurred_at=month_start_utc() - timedelta(days=1),
        )
    )
    db_session.commit()
    b = Budget(db_session, profile, posting_id=posting.id)
    assert b.month_units("serper") == 0
    b.spend("websearch", backend="serper")
    b.spend("websearch", backend="serper")
    ok, why = b.can_spend("websearch", backend="serper")
    assert ok is False and "monthly" in why
    assert b.can_spend("websearch", backend="ddg") == (True, "ok")  # ddg has no monthly limit


def test_paid_provider_uses_usd_cap_and_refuses_unpriced(home, db_session, posting) -> None:
    profile = load_profile()
    profile.contacts.provider_limits["hunter"].paid = True
    profile.contacts.monthly_usd_cap = 0.03
    b = Budget(db_session, profile, posting_id=posting.id)
    b.spend("hunter", 1.0)  # $0.0245
    ok, why = b.can_spend("hunter", 1.0)  # would reach $0.049 > cap
    assert ok is False and "usd cap" in why
    assert round(b.month_usd(), 4) == 0.0245
    profile.contacts.provider_limits["apollo"].paid = True
    ok, why = b.can_spend("apollo", 1.0)
    assert ok is False and "no confirmed price" in why


def test_unknown_provider_is_free_and_uncapped(home, db_session, posting) -> None:
    b = Budget(db_session, load_profile(), posting_id=posting.id)
    assert b.can_spend("llm") == (True, "ok")
    b.spend("llm")
    assert b.job_units["llm"] == 1.0
