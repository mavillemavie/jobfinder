from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from jobfinder.db.models import AdapterHealth, utcnow

DEFAULT_TZ = "America/Montreal"


def today_in(tz: str = DEFAULT_TZ, now: datetime | None = None) -> str:
    """Calendar date (ISO) in the product timezone; `now` must be aware when given."""
    moment = now or datetime.now(ZoneInfo(tz))
    return moment.astimezone(ZoneInfo(tz)).date().isoformat()


def _now():
    return utcnow().replace(tzinfo=None)


class AdapterGuard:
    """Per-adapter health (cooldown, last error, last ok) plus its daily call budget.

    `budget_adapter` splits the two when several adapters share one pooled cap: health —
    cooldown, last_error, last_ok_at — is tracked on the `adapter` row, while the call
    counter and the daily cap are read from and written to the `budget_adapter` row. That
    keeps a 429 from one ATS board from silently benching every other board (I3).
    """

    def __init__(
        self,
        session: Session,
        adapter: str,
        daily_cap: int | None = None,
        tz: str = DEFAULT_TZ,
        budget_adapter: str | None = None,
    ) -> None:
        self.session = session
        self.adapter = adapter
        self.daily_cap = daily_cap
        self.tz = tz
        self.budget_adapter = budget_adapter or adapter

    def _row_for(self, adapter: str) -> AdapterHealth:
        row = self.session.get(AdapterHealth, adapter)
        if row is None:
            row = AdapterHealth(
                adapter=adapter,
                calls_today=0,
                calls_date=today_in(self.tz),
            )
            self.session.add(row)
            self.session.flush()
        if row.calls_date != today_in(self.tz):
            row.calls_date = today_in(self.tz)
            row.calls_today = 0
        return row

    def row(self) -> AdapterHealth:
        """The health row for this adapter (cooldown / last_error / last_ok_at)."""
        return self._row_for(self.adapter)

    def budget_row(self) -> AdapterHealth:
        """The row holding the call counter — the pooled row when one is configured."""
        return self._row_for(self.budget_adapter)

    def allowed(self) -> tuple[bool, str]:
        row = self.row()
        if row.cooldown_until and row.cooldown_until > _now():
            return False, f"cooldown until {row.cooldown_until:%Y-%m-%d %H:%M} UTC"
        if self.daily_cap is not None and self.budget_row().calls_today >= self.daily_cap:
            return False, f"daily cap {self.daily_cap} reached"
        return True, "ok"

    def remaining(self) -> int | None:
        if self.daily_cap is None:
            return None
        return max(self.daily_cap - self.budget_row().calls_today, 0)

    def add_calls(self, n: int) -> None:
        """Commits the session (as do record_ok/record_error) — health must survive
        whatever the adapter does next, including a rollback of its own work."""
        self.budget_row().calls_today += n
        self.session.commit()

    def record_ok(self) -> None:
        """Commits the session."""
        row = self.row()
        row.last_ok_at = _now()
        row.last_error = None
        row.cooldown_until = None
        self.session.commit()

    def record_error(self, message: str, cooldown_s: int = 0) -> None:
        """Commits the session."""
        row = self.row()
        row.last_error = message[:2000]
        if cooldown_s > 0:
            row.cooldown_until = _now() + timedelta(seconds=min(cooldown_s, 86400))
        self.session.commit()
