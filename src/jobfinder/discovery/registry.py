from __future__ import annotations

from jobfinder.config import Profile
from jobfinder.discovery.base import JobSource
from jobfinder.discovery.fetch import HttpClient
from jobfinder.discovery.sources.active_jobs_db import ActiveJobsDbSource
from jobfinder.discovery.sources.adzuna import AdzunaSource
from jobfinder.discovery.sources.ats.ashby import AshbySource
from jobfinder.discovery.sources.ats.base import CompaniesFor
from jobfinder.discovery.sources.ats.greenhouse import GreenhouseSource
from jobfinder.discovery.sources.ats.lever import LeverSource
from jobfinder.discovery.sources.ats.smartrecruiters import SmartRecruitersSource
from jobfinder.discovery.sources.ats.workable import WorkableSource
from jobfinder.discovery.sources.feeds import JobBankRssSource, RemotiveSource, WeWorkRemotelySource
from jobfinder.discovery.sources.jooble import JoobleSource
from jobfinder.discovery.sources.jsearch import JSearchSource
from jobfinder.discovery.sources.linkedin_guest import LinkedInGuestSource
from jobfinder.discovery.sources.linkedin_jobs import LinkedInJobsSource
from jobfinder.discovery.sources.reed import ReedSource
from jobfinder.settings import Settings


def build_sources(
    profile: Profile, settings: Settings, client: HttpClient, companies_for: CompaniesFor
) -> tuple[list[JobSource], dict[str, str]]:
    sources: list[JobSource] = []
    skipped: dict[str, str] = {}

    def want(name: str, *keys: str) -> bool:
        if not profile.source_enabled(name):
            skipped[name] = "disabled"
            return False
        if keys and not settings.has(*keys):
            skipped[name] = f"missing {', '.join(k.upper() for k in keys)}"
            return False
        return True

    if want("adzuna", "adzuna_app_id", "adzuna_app_key"):
        sources.append(AdzunaSource(client, settings.adzuna_app_id, settings.adzuna_app_key))
    if want("reed", "reed_api_key"):
        sources.append(ReedSource(client, settings.reed_api_key))
    if want("jooble", "jooble_api_key"):
        sources.append(JoobleSource(client, settings.jooble_api_key))
    if want("jsearch", "rapidapi_key"):
        sources.append(JSearchSource(client, settings.rapidapi_key))
    if want("jobbank_rss"):
        sources.append(JobBankRssSource(client))
    if want("remotive"):
        sources.append(RemotiveSource(client))
    if want("weworkremotely"):
        sources.append(WeWorkRemotelySource(client))
    if want("linkedin_guest"):
        sources.append(LinkedInGuestSource(client))
    fantastic = (("linkedin_jobs", LinkedInJobsSource), ("active_jobs_db", ActiveJobsDbSource))
    for name, cls in fantastic:
        if want(name, "rapidapi_key"):
            cfg = profile.sources.get(name)
            sources.append(cls(
                client, settings.rapidapi_key,
                page_size=cfg.page_size if cfg else None,
                exclude_organizations=cfg.exclude_organizations if cfg else None,
                agencies=cfg.agencies if cfg else "include",
            ))
    if want("ats_boards"):
        ats_classes = (
            GreenhouseSource, LeverSource, AshbySource, WorkableSource, SmartRecruitersSource,
        )
        for cls in ats_classes:
            sources.append(cls(client, companies_for))
    return sources, skipped
