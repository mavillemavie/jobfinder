from __future__ import annotations

import re
from urllib.parse import urlparse

from jobfinder.contacts.base import ContactContext, StepResult
from jobfinder.discovery.normalize import normalize_company

# Hosts that never belong to the hiring company: job boards, ATS vendors, aggregators, social.
JOB_HOSTS = (
    "linkedin.com", "indeed.", "glassdoor.", "jobbank.gc.ca", "greenhouse.io", "lever.co",
    "ashbyhq.com", "workable.com", "smartrecruiters.com", "remotive.", "weworkremotely.com",
    "adzuna.", "reed.co.uk", "jooble.", "ziprecruiter.", "monster.", "workopolis.", "eluta.ca",
    "jobillico.", "talent.com", "simplyhired.", "careerjet.", "neuvoo.", "google.", "facebook.com",
    "x.com", "twitter.com", "instagram.com", "youtube.com", "wikipedia.org", "bloomberg.com",
    "crunchbase.com", "zoominfo.com", "apollo.io", "duckduckgo.com", "bing.com", "yelp.",
)
_TWO_LEVEL_TLDS = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "co.nz", "com.br", "co.jp"}
_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def registrable_domain(host: str | None) -> str | None:
    host = (host or "").split(":")[0].strip().lower().rstrip(".")
    if not host or _IP.match(host):
        return None
    labels = host.split(".")
    if len(labels) < 2:
        return None
    if ".".join(labels[-2:]) in _TWO_LEVEL_TLDS:
        return ".".join(labels[-3:]) if len(labels) >= 3 else None
    return ".".join(labels[-2:])


def is_job_host(host: str | None) -> bool:
    h = (host or "").lower()
    return any(j in h for j in JOB_HOSTS)


def domain_from_apply_url(url: str | None) -> str | None:
    host = urlparse(url or "").netloc
    if not host or is_job_host(host):
        return None
    return registrable_domain(host)


def _set(ctx: ContactContext, domain: str, website: str) -> None:
    ctx.domain, ctx.website = domain, website
    ctx.company.domain = domain
    ctx.company.website = ctx.company.website or website
    if not ctx.company.country and ctx.posting.country:
        ctx.company.country = ctx.posting.country


class CompanyResolveStep:
    name = "company_resolve"

    def run(self, ctx: ContactContext) -> StepResult:
        co = ctx.company
        if co.domain:
            ctx.domain, ctx.website = co.domain, co.website or f"https://{co.domain}"
            return StepResult(self.name, notes={"domain": ctx.domain, "how": "cached"})
        d = domain_from_apply_url(ctx.posting.apply_url)
        if d:
            _set(ctx, d, f"https://{d}")
            return StepResult(self.name, notes={"domain": d, "how": "apply_url"})
        if ctx.search is None:
            return StepResult(self.name, skipped="no search backend")
        gl, hl = ctx.gl_hl
        res = ctx.search.search(f'"{co.name}" official site', gl=gl, hl=hl, num=5)
        if res is None:
            return StepResult(self.name, skipped=ctx.search.last_reason)
        credits = {"websearch": 1.0}
        ctx.knowledge_graph = res.knowledge_graph or {}
        notes: dict = {"query": res.query, "backend": res.backend}
        kg_site = ctx.knowledge_graph.get("website")
        if kg_site:
            d = registrable_domain(urlparse(str(kg_site)).netloc)
            if d and not is_job_host(d):
                _set(ctx, d, f"https://{d}")
                return StepResult(
                    self.name, credits=credits,
                    notes={**notes, "domain": d, "how": "knowledge_graph"},
                )
        target = normalize_company(co.name)
        apply_host = urlparse(ctx.posting.apply_url or "").netloc.lower()
        for hit in res.hits:
            host = urlparse(hit.link).netloc.lower()
            if not host or is_job_host(host):
                continue
            d = registrable_domain(host)
            if not d:
                continue
            if (apply_host and d in apply_host) or (
                target and target in normalize_company(hit.title)
            ):
                _set(ctx, d, f"https://{d}")
                return StepResult(
                    self.name, credits=credits, notes={**notes, "domain": d, "how": "search"}
                )
        return StepResult(
            self.name, ok=False, credits=credits, notes=notes, error="no matching result"
        )
