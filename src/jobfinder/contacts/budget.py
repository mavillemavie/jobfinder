from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobfinder.config import Profile
from jobfinder.db.models import SpendLedger, utcnow


class BudgetExhausted(Exception):
    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(f"{provider}: {reason}")
        self.provider = provider
        self.reason = reason


def month_start_utc() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


class Budget:
    """Per-job caps + monthly free-unit counters + monthly USD cap, backed by spend_ledger."""

    def __init__(self, session: Session, profile: Profile, posting_id: int | None) -> None:
        self.session = session
        self.profile = profile
        self.posting_id = posting_id
        self.job_units: dict[str, float] = {}

    def _limit_key(self, provider: str, backend: str | None) -> str | None:
        if provider == "websearch":
            return backend if backend in self.profile.contacts.provider_limits else None
        return provider if provider in self.profile.contacts.provider_limits else None

    def month_units(self, limit_key: str) -> float:
        total = self.session.scalar(
            select(func.coalesce(func.sum(SpendLedger.credits), 0.0)).where(
                SpendLedger.provider == limit_key, SpendLedger.occurred_at >= month_start_utc()
            )
        )
        return float(total or 0.0)

    def month_usd(self) -> float:
        total = self.session.scalar(
            select(func.coalesce(func.sum(SpendLedger.usd_estimate), 0.0)).where(
                SpendLedger.occurred_at >= month_start_utc()
            )
        )
        return float(total or 0.0)

    def can_spend(
        self, provider: str, units: float = 1.0, *, backend: str | None = None
    ) -> tuple[bool, str]:
        cap = self.profile.contacts.per_job_credit_cap.get(provider)
        if cap is not None and self.job_units.get(provider, 0.0) + units > cap + 1e-9:
            return False, f"per-job cap {cap} for {provider} reached"
        key = self._limit_key(provider, backend)
        if key is None:
            return True, "ok"
        limit = self.profile.contacts.provider_limits[key]
        if not limit.paid:
            if self.month_units(key) + units > limit.monthly_units + 1e-9:
                return False, f"monthly free units for {key} exhausted ({limit.monthly_units})"
            return True, "ok"
        if limit.usd_per_unit is None:
            return False, f"no confirmed price for {key}; paid calls refused"
        cost = units * limit.usd_per_unit
        if self.month_usd() + cost > self.profile.contacts.monthly_usd_cap + 1e-9:
            return False, (
                f"monthly usd cap {self.profile.contacts.monthly_usd_cap} would be exceeded"
            )
        return True, "ok"

    def spend(self, provider: str, units: float = 1.0, *, backend: str | None = None) -> None:
        ok, why = self.can_spend(provider, units, backend=backend)
        if not ok:
            raise BudgetExhausted(provider, why)
        key = self._limit_key(provider, backend)
        usd = 0.0
        if key is not None:
            limit = self.profile.contacts.provider_limits[key]
            if limit.paid and limit.usd_per_unit is not None:
                usd = units * limit.usd_per_unit
        self.session.add(
            SpendLedger(
                provider=key or provider,
                credits=units,
                usd_estimate=usd,
                posting_id=self.posting_id,
                occurred_at=utcnow().replace(tzinfo=None),
            )
        )
        self.session.flush()
        self.job_units[provider] = self.job_units.get(provider, 0.0) + units
