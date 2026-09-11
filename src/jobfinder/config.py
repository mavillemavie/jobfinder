from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from jobfinder import paths

log = logging.getLogger(__name__)


class TitleCluster(BaseModel):
    name: str
    include: list[str]


class TitlesConfig(BaseModel):
    clusters: list[TitleCluster]
    exclude_words: list[str] = Field(default_factory=list)
    # Terms actually sent to the job boards. Empty = the first two terms of each cluster (the
    # historical default). Every cluster term still filters titles; only these are searched.
    search_keywords: list[str] = Field(default_factory=list)
    seniority_allowed: list[str] = Field(
        default_factory=lambda: ["junior", "intermediate", "senior", "lead", "unspecified"]
    )


class LocationEntry(BaseModel):
    key: str
    enabled: bool = True
    country: str | None = None
    region: str | None = None
    cities: list[str] = Field(default_factory=list)
    remote_scope: list[str] = Field(default_factory=list)


class ScoringConfig(BaseModel):
    match_threshold: int = 70
    max_llm_scored_per_run: int = 40
    max_posting_age_days: int = 14
    min_keyword_overlap: int = 3
    max_hydrate_per_run: int = 30
    hydrate_delay_s: float = 1.5
    # Free text the scorer reads as the candidate's eligibility (visa status, willingness to
    # relocate). Facts about JF that the resume does not carry; never resume claims.
    candidate_note: str = ""


class SourceConfig(BaseModel):
    enabled: bool = True
    daily_calls: int | None = None
    page_size: int | None = None  # results per request, for sources whose credits count jobs
    # Employers whose postings are excluded at the source (server-side), for sources that
    # support it: e.g. an aggregator re-posting the same "new grad" roles under its own name.
    exclude_organizations: list[str] = Field(default_factory=list)
    # Recruitment/staffing agencies: "include" (default), "exclude", or "only" — for sources
    # that flag them server-side. Agency posts hide the employer the contact waterfall targets.
    agencies: Literal["include", "exclude", "only"] = "include"


class ProviderLimit(BaseModel):
    monthly_units: float = 0
    usd_per_unit: float | None = None
    paid: bool = False


class ContactsConfig(BaseModel):
    auto_run_on_match: bool = True
    # Run the contact waterfall when JF shortlists a posting (and catch up nightly for
    # shortlisted matches without a run). With auto_run_on_match off this is what spends
    # Hunter credits only on jobs JF actually wants.
    run_on_shortlist: bool = True
    monthly_usd_cap: float = 50
    per_job_credit_cap: dict[str, int] = Field(
        default_factory=lambda: {"apollo": 3, "hunter": 2, "websearch": 6}
    )
    stop_when: str = "verified_email_and_any_phone"
    provider_limits: dict[str, ProviderLimit] = Field(
        default_factory=lambda: {
            "hunter": ProviderLimit(monthly_units=45, usd_per_unit=0.0245),
            "serper": ProviderLimit(monthly_units=300, usd_per_unit=0.001),
            "apollo": ProviderLimit(monthly_units=75, usd_per_unit=None),
        }
    )
    max_runs_per_scan: int = 10
    search_fallback: Literal["ddg", "none"] = "ddg"


class ScheduleConfig(BaseModel):
    run_at: str = "06:30"
    timezone: str = "America/Montreal"


class DigestConfig(BaseModel):
    enabled: bool = True
    send_at: str = "07:00"
    timezone: str = "America/Montreal"


class DashboardConfig(BaseModel):
    port: int = 3838
    public_base_url: str = "http://localhost:3838"
    bind_host: str = "0.0.0.0"
    allowed_client_cidrs: list[str] = Field(
        default_factory=lambda: ["127.0.0.0/8", "::1/128", "100.64.0.0/10"]
    )


class DocumentsConfig(BaseModel):
    languages: Literal["auto", "en", "fr"] = "auto"
    file_name_pattern: str = "{kind}-{company}"


class Profile(BaseModel):
    titles: TitlesConfig
    locations: list[LocationEntry]
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    contacts: ContactsConfig = Field(default_factory=ContactsConfig)
    scan: ScheduleConfig = Field(default_factory=ScheduleConfig)
    digest: DigestConfig = Field(default_factory=DigestConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    documents: DocumentsConfig = Field(default_factory=DocumentsConfig)

    def enabled_locations(self) -> list[LocationEntry]:
        return [loc for loc in self.locations if loc.enabled]

    def location_rank(self, key: str | None) -> int:
        """Position of a location key in the enabled list; unknown keys sort last. Hydration
        and scoring are capped per run and take postings in this order, so the list order in
        profile.yaml is a priority (Canada entries before uk)."""
        keys = [loc.key for loc in self.enabled_locations()]
        return keys.index(key) if key in keys else len(keys)

    def title_terms(self) -> list[str]:
        return [t.lower() for c in self.titles.clusters for t in c.include]

    def source_enabled(self, name: str) -> bool:
        cfg = self.sources.get(name)
        return True if cfg is None else cfg.enabled

    def source_daily_cap(self, name: str) -> int | None:
        cfg = self.sources.get(name)
        return None if cfg is None else cfg.daily_calls


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 120
    return y


def load_profile(path: Path | None = None) -> Profile:
    if path is None:
        path = paths.config_path()
        if not path.exists():
            # Fresh clone: the personal profile is gitignored, start from the template so
            # Settings has a real file to write back into.
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(paths.example_profile_path(), path)
            log.warning("created %s from %s", path, paths.example_profile_path().name)
    with path.open() as f:
        data = _yaml().load(f)
    return Profile.model_validate(data)


def save_profile(profile: Profile, path: Path | None = None) -> None:
    """Write back into the existing document so comments survive."""
    path = path or paths.config_path()
    y = _yaml()
    with path.open() as f:
        doc = y.load(f)
    _merge_into(doc, profile.model_dump(mode="json"))
    with path.open("w") as f:
        y.dump(doc, f)


def _merge_into(doc: Any, data: dict[str, Any]) -> None:
    """Merge model data into the round-trip document: existing nodes keep their comments and
    flow/block style; keys absent from the file are only added when they carry a real value."""
    for key, value in data.items():
        if key not in doc and value in (None, [], {}):
            continue
        doc[key] = _merge_value(doc.get(key), value)


def _merge_value(old: Any, new: Any) -> Any:
    if isinstance(new, dict):
        if isinstance(old, CommentedMap):
            _merge_into(old, new)
            return old
        return new
    if isinstance(new, list):
        if isinstance(old, CommentedSeq):
            # Mutate in place so item comments and the flow/block style survive.
            for i, item in enumerate(new):
                if i < len(old):
                    old[i] = _merge_value(old[i], item)
                else:
                    old.append(_merge_value(None, item))
            del old[len(new):]
            return old
        return new
    # Unchanged scalars keep their original object (and thus their quoting style).
    return old if old == new and old is not None else new
