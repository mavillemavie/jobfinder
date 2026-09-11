"""Employer career-site postings via Fantastic Jobs' "Active Jobs DB" on RapidAPI.

54 ATS platforms (Greenhouse, Lever, Ashby, Workday, …) across 200k employers, refreshed
hourly, full text. Fills the slot our five ATS board adapters leave empty: they need a board
token per company and none was ever discovered from aggregator links. One request a day.
"""
from __future__ import annotations

from jobfinder.discovery.sources.fantastic_jobs import FantasticJobsSource

HOST = "active-jobs-db.p.rapidapi.com"


class ActiveJobsDbSource(FantasticJobsSource):
    name = "active_jobs_db"
    host = HOST
    path = "/active-ats"
