"""LinkedIn postings via Fantastic Jobs' "LinkedIn Job Search API" on RapidAPI.

Replaces the guest scraper (429 on every call after day 0): one filtered request a day,
server-side title and country filters, full description text, no fetch against linkedin.com.
"""
from __future__ import annotations

from jobfinder.discovery.sources.fantastic_jobs import DEFAULT_PAGE_SIZE, FantasticJobsSource

HOST = "linkedin-job-search-api.p.rapidapi.com"
ACTIVE = f"https://{HOST}/active-jb"
__all__ = ["ACTIVE", "DEFAULT_PAGE_SIZE", "HOST", "LinkedInJobsSource"]


class LinkedInJobsSource(FantasticJobsSource):
    name = "linkedin_jobs"
    host = HOST
    path = "/active-jb"
