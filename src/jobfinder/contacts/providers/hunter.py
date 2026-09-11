from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jobfinder.discovery.fetch import HttpClient

BASE = "https://api.hunter.io/v2"


@dataclass
class HunterDomain:
    pattern: str | None
    accept_all: bool | None
    sample_email: str | None
    organization: str | None


@dataclass
class HunterFinder:
    email: str | None
    score: int
    status: str | None


@dataclass
class HunterVerify:
    status: str
    result: str | None
    score: int
    accept_all: bool
    smtp_check: bool
    mx_records: bool


class HunterClient:
    name = "hunter"

    def __init__(self, http: HttpClient, api_key: str) -> None:
        self.http, self.api_key = http, api_key

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = self.http.get_json(f"{BASE}/{path}", params={**params, "api_key": self.api_key})
        return payload.get("data") or {}

    def domain_search(self, domain: str, *, limit: int = 1) -> HunterDomain:
        """1 credit per email returned → limit=1 buys the pattern for 1 credit (playbook step 5)."""
        d = self._get("domain-search", {"domain": domain, "limit": limit})
        emails = d.get("emails") or []
        return HunterDomain(
            d.get("pattern"),
            d.get("accept_all"),
            (emails[0].get("value") if emails else None),
            d.get("organization"),
        )

    def email_finder(self, domain: str, first_name: str, last_name: str) -> HunterFinder:
        """1 credit when an email is returned."""
        d = self._get(
            "email-finder", {"domain": domain, "first_name": first_name, "last_name": last_name}
        )
        return HunterFinder(
            d.get("email"), int(d.get("score") or 0), (d.get("verification") or {}).get("status")
        )

    def email_verifier(self, email: str) -> HunterVerify:
        """0.5 credit."""
        d = self._get("email-verifier", {"email": email})
        return HunterVerify(
            str(d.get("status") or "unknown"),
            d.get("result"),
            int(d.get("score") or 0),
            bool(d.get("accept_all")),
            bool(d.get("smtp_check")),
            bool(d.get("mx_records")),
        )
