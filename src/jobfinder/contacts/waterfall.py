from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobfinder.config import Profile
from jobfinder.contacts.base import ContactContext, StepResult
from jobfinder.contacts.budget import Budget, BudgetExhausted
from jobfinder.contacts.providers.apollo import ApolloClient
from jobfinder.contacts.providers.hunter import HunterClient
from jobfinder.contacts.providers.search import DuckDuckGoClient, SerperClient, WebSearch
from jobfinder.contacts.steps.assemble import AssembleStep
from jobfinder.contacts.steps.company_resolve import CompanyResolveStep
from jobfinder.contacts.steps.email_pattern import EmailPatternStep
from jobfinder.contacts.steps.email_resolve import EmailResolveStep
from jobfinder.contacts.steps.people_search import PeopleSearchStep
from jobfinder.contacts.steps.phone import PhoneStep
from jobfinder.contacts.steps.posting_extract import PostingExtractStep
from jobfinder.contacts.steps.target_inference import TargetInferenceStep
from jobfinder.db.models import ContactRun, Pipeline, Posting, utcnow
from jobfinder.db.session import release_snapshot
from jobfinder.discovery.base import SourceError
from jobfinder.discovery.fetch import HttpClient
from jobfinder.llm.base import LLMError, LLMProvider
from jobfinder.settings import Settings

log = logging.getLogger(__name__)

STEP_ORDER: tuple[type, ...] = (
    PostingExtractStep, TargetInferenceStep, CompanyResolveStep, PeopleSearchStep,
    EmailPatternStep, EmailResolveStep, PhoneStep, AssembleStep,
)


@dataclass
class Clients:
    search: WebSearch | None
    apollo: ApolloClient | None
    hunter: HunterClient | None


def build_clients(
    settings: Settings, profile: Profile, http: HttpClient, budget: Budget
) -> Clients:
    serper = SerperClient(http, settings.serper_api_key) if settings.serper_api_key else None
    ddg = DuckDuckGoClient(http) if profile.contacts.search_fallback == "ddg" else None
    search = WebSearch(serper, ddg, budget) if (serper or ddg) else None
    apollo = ApolloClient(http, settings.apollo_api_key) if settings.apollo_api_key else None
    hunter = HunterClient(http, settings.hunter_api_key) if settings.hunter_api_key else None
    return Clients(search, apollo, hunter)


def _run_step(step, ctx: ContactContext) -> tuple[StepResult, bool]:
    """Run one step; never raise. Returns (result, is_error)."""
    try:
        return step.run(ctx), False
    except BudgetExhausted as exc:
        return StepResult(step.name, skipped=f"budget: {exc.reason}"), False
    except LLMError as exc:
        return StepResult(step.name, ok=False, error=f"llm: {exc}"[:300]), True
    except SourceError as exc:  # RateLimited and BudgetExceeded are SourceErrors
        return StepResult(step.name, ok=False, error=str(exc)[:300]), True
    except Exception as exc:  # noqa: BLE001 — one step never stops the floor
        log.exception("contact step %s failed", step.name)
        return StepResult(step.name, ok=False, error=repr(exc)[:300]), True


def run_for_posting(
    session: Session,
    posting_id: int,
    *,
    llm: LLMProvider,
    settings: Settings,
    profile: Profile,
    http: HttpClient | None = None,
    clients: Clients | None = None,
) -> ContactRun:
    posting = session.get(Posting, posting_id)
    if posting is None:
        raise ValueError(f"no posting {posting_id}")
    run = ContactRun(posting_id=posting_id, status="running")
    session.add(run)
    session.commit()
    http = http or HttpClient()
    budget = Budget(session, profile, posting_id)
    c = clients or build_clients(settings, profile, http, budget)
    ctx = ContactContext(
        session=session, posting=posting, company=posting.company, profile=profile,
        settings=settings, llm=llm, http=http, budget=budget, search=c.search, apollo=c.apollo,
        hunter=c.hunter, language=posting.language if posting.language in ("en", "fr") else "en",
    )
    steps: list[dict] = []
    errors = 0
    stopped = False
    outcome: str | None = None
    try:
        for step_cls in STEP_ORDER:
            step = step_cls()
            if stopped and step.name != "assemble":
                steps.append(asdict(StepResult(step.name, skipped="stop_when satisfied")))
                continue
            # Each step is an LLM or HTTP call: end the read transaction first so the
            # writes after it never start on a snapshot another thread has made stale.
            release_snapshot(session)
            result, is_error = _run_step(step, ctx)
            errors += int(is_error)
            steps.append(asdict(result))
            if step.name == "assemble":
                outcome = result.notes.get("outcome") if result.ok else None
            elif not stopped and ctx.stop_satisfied():
                stopped = True
        run.steps = steps
        run.credits = dict(budget.job_units)
        run.finished_at = utcnow().replace(tzinfo=None)
        if outcome is None:
            run.status = "error"
        elif outcome == "needs_manual":
            run.status = "manual"
        else:
            run.status = "partial" if errors else "ok"
        session.commit()
    except Exception:
        session.rollback()
        run = session.get(ContactRun, run.id)
        run.status = "error"
        run.steps = steps
        run.finished_at = utcnow().replace(tzinfo=None)
        session.commit()
        raise
    return run


def pending_matches(session: Session, limit: int, stage: str | None = None) -> list[Posting]:
    """Matches without a contact run; with `stage`, only those whose pipeline is at it."""
    done = select(ContactRun.posting_id)
    q = select(Posting).where(Posting.status == "match", Posting.id.not_in(done))
    if stage:
        q = q.where(Posting.pipeline.has(Pipeline.stage == stage))
    return list(session.scalars(q.order_by(Posting.id).limit(limit)))


def run_for_new_matches(
    session: Session,
    *,
    llm: LLMProvider,
    settings: Settings,
    profile: Profile,
    http: HttpClient | None = None,
    limit: int | None = None,
    stage: str | None = None,
) -> dict:
    stats = {"runs": 0, "ok": 0, "partial": 0, "manual": 0, "error": 0}
    http = http or HttpClient()
    for p in pending_matches(session, limit or profile.contacts.max_runs_per_scan, stage=stage):
        try:
            run = run_for_posting(
                session, p.id, llm=llm, settings=settings, profile=profile, http=http
            )
        except Exception:  # noqa: BLE001 — one posting never stops the batch
            log.exception("contact run failed for posting %s", p.id)
            session.rollback()
            stats["error"] += 1
            continue
        stats["runs"] += 1
        stats[run.status] = stats.get(run.status, 0) + 1
    return stats
