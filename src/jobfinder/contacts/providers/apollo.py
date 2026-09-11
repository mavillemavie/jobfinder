from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jobfinder.discovery.fetch import HttpClient

BASE = "https://api.apollo.io/api/v1"


@dataclass
class ApolloPerson:
    id: str
    first_name: str
    last_name_obfuscated: str
    title: str
    has_email: bool
    has_direct_phone: str | None
    organization_name: str | None


@dataclass
class ApolloMatch:
    email: str | None
    title: str | None
    first_name: str | None
    last_name: str | None
    phone_numbers: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class ApolloClient:
    name = "apollo"

    def __init__(self, http: HttpClient, api_key: str) -> None:
        self.http, self.api_key = http, api_key

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }

    def people_search(
        self, domain: str, titles: list[str], *, per_page: int = 5
    ) -> list[ApolloPerson]:
        """0 credits. Filters are QUERY PARAMS (api-notes: Apollo).

        Returns booleans (`has_email`, `has_direct_phone`) only, never values.
        """
        params: list[tuple[str, str]] = [
            ("q_organization_domains_list[]", domain),
            ("per_page", str(per_page)),
            ("page", "1"),
        ]
        params += [("person_titles[]", t) for t in titles]
        payload = self.http.post_json_with_params(
            f"{BASE}/mixed_people/api_search", params=params, headers=self._headers()
        )
        out: list[ApolloPerson] = []
        for p in payload.get("people", []):
            try:
                out.append(
                    ApolloPerson(
                        id=str(p.get("id", "")),
                        first_name=str(p.get("first_name") or ""),
                        last_name_obfuscated=str(p.get("last_name_obfuscated") or ""),
                        title=str(p.get("title") or ""),
                        has_email=bool(p.get("has_email")),
                        has_direct_phone=p.get("has_direct_phone") or None,
                        organization_name=(p.get("organization") or {}).get("name"),
                    )
                )
            except (TypeError, ValueError, AttributeError):
                continue
        return out

    def people_match(self, first_name: str, last_name: str, domain: str) -> ApolloMatch | None:
        """1 credit when a person is found (caller spends). Phone reveal is NOT requested (v1)."""
        payload = self.http.post_json(
            f"{BASE}/people/match",
            {
                "first_name": first_name,
                "last_name": last_name,
                "domain": domain,
                "reveal_personal_emails": False,
                "reveal_phone_number": False,
            },
            headers=self._headers(),
        )
        person = payload.get("person")
        if not person:
            return None
        return ApolloMatch(
            email=person.get("email"),
            title=person.get("title"),
            first_name=person.get("first_name"),
            last_name=person.get("last_name"),
            phone_numbers=[
                str(n.get("sanitized_number") or n.get("raw_number") or "")
                for n in person.get("phone_numbers", [])
                if isinstance(n, dict)
            ],
            raw=person,
        )
