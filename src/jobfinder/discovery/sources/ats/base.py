from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from jobfinder.db.models import Company
from jobfinder.discovery.base import RawPosting, SearchProfile, SourceError, filter_by_title
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient, RateLimited

CompaniesFor = Callable[[str], list[Company]]

log = logging.getLogger(__name__)


class ATSBoardSource:
    ats_type = ""
    name = ""

    def __init__(self, client: HttpClient, companies_for: CompaniesFor) -> None:
        self.client = client
        self.companies_for = companies_for

    def fetch(self, profile: SearchProfile, since: datetime) -> list[RawPosting]:
        out: list[RawPosting] = []
        for company in self.companies_for(self.ats_type):
            token = company.ats_board_token
            if not token:
                continue
            try:
                raws = self.fetch_board(token, profile)
            except BudgetExceeded:
                # Budget is a soft stop: keep the boards fetched so far.
                break
            except RateLimited:
                # Never swallowed as a per-board failure — run_scan must see it to set
                # the adapter's cooldown.
                raise
            except SourceError as exc:
                log.warning("%s: board %s failed: %s", self.name, token, exc)
                continue
            for r in raws:
                r.company_name = company.name
            out.extend(filter_by_title(raws, profile))
        return out

    def fetch_board(self, token: str, profile: SearchProfile) -> list[RawPosting]:
        return self.parse_board(self.client.get_json(self.board_url(token)), token)

    def board_url(self, token: str) -> str:
        raise NotImplementedError

    def parse_board(self, payload: Any, token: str) -> list[RawPosting]:
        raise NotImplementedError
