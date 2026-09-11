from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from jobfinder.db.models import Posting
from jobfinder.discovery.base import SourceError
from jobfinder.discovery.dedupe import content_hash
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient, RateLimited, extract_description
from jobfinder.discovery.normalize import detect_ats, detect_language

log = logging.getLogger(__name__)

_LINKEDIN_VIEW_MARKER = "linkedin.com/jobs/view/"
_TRAILING_DIGITS = re.compile(r"(\d+)/?(?:\?.*)?$")

# After this many failed fetches the posting is scored on its snippet instead of
# consuming a hydration slot on every future run.
MAX_HYDRATE_ATTEMPTS = 3


def _cleared(prefilter_result: dict | None) -> dict:
    """A copy of the prefilter result with the hydration-failure bookkeeping dropped."""
    out = {**(prefilter_result or {})}
    out.pop("hydrate_error", None)
    out.pop("hydrate_attempts", None)
    return out


def _hydrate_url(posting: Posting) -> str:
    """URL to fetch full text from for this posting.

    LinkedIn's public job-view page increasingly bounces guests to a login wall;
    the guest detail endpoint serves the same posting body without auth, keyed by
    the job id (the trailing digits of the view URL). `apply_url` still points at
    the view page — that's the link a human clicks — this only changes what we fetch.
    """
    if _LINKEDIN_VIEW_MARKER in posting.apply_url:
        match = _TRAILING_DIGITS.search(posting.apply_url)
        if match:
            return f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{match.group(1)}"
    return posting.apply_url


def hydrate_posting(session: Session, posting: Posting, client: HttpClient) -> bool:
    """Fetch and extract the full job description from posting.apply_url.

    Returns True if description was updated, False otherwise.

    Fetch failures are counted in `prefilter_result["hydrate_attempts"]`; after
    MAX_HYDRATE_ATTEMPTS the posting is marked `description_complete` with
    `hydrate_note="gave_up"` so it is scored on its snippet rather than retried
    forever. A later success clears both keys.

    Raises:
        RateLimited: when the fetch encounters a 429. Callers must catch this
            to stop the hydration pass for the run.
        BudgetExceeded: when the client's call budget is exhausted. Callers must
            catch this to stop the hydration pass for the run.
    """
    if posting.description_complete:
        return False
    fetch_url = _hydrate_url(posting)
    try:
        html = client.get_text(fetch_url)
    except (RateLimited, BudgetExceeded):
        raise
    except SourceError as exc:
        msg = str(exc)
        code = msg[:3] if msg[:3].isdigit() else type(exc).__name__
        result = {**(posting.prefilter_result or {})}
        attempts = int(result.get("hydrate_attempts") or 0) + 1
        result["hydrate_error"] = code
        result["hydrate_attempts"] = attempts
        if attempts >= MAX_HYDRATE_ATTEMPTS:
            # Give up: mark it complete so it is scored on the snippet it already has and
            # stops eating a slot out of max_hydrate_per_run on every future run.
            posting.description_complete = True
            result["hydrate_note"] = "gave_up"
        posting.prefilter_result = result
        log.warning(
            "hydrate failed for posting %s (attempt %s): %s", posting.id, attempts, exc
        )
        session.flush()
        return False
    final_url = client.last_final_url or fetch_url
    ats = detect_ats(final_url)
    if ats and posting.company is not None and not posting.company.ats_type:
        posting.company.ats_type, posting.company.ats_board_token = ats
    # extract_description keys its selector on a substring of the URL. For LinkedIn
    # we fetched the guest endpoint, but its markup should still be read against the
    # view URL so DESCRIPTION_SELECTORS["linkedin.com"] is the one applied.
    selector_url = posting.apply_url if fetch_url != posting.apply_url else final_url
    text = extract_description(html, selector_url)[:20000]
    if len(text) <= len(posting.description_text):
        posting.description_complete = True
        cleared = _cleared(posting.prefilter_result)
        posting.prefilter_result = {**cleared, "hydrate_note": "not_longer"}
        session.flush()
        return False
    posting.prefilter_result = _cleared(posting.prefilter_result) or None
    posting.description_text = text
    posting.content_hash = content_hash(text)
    posting.description_complete = True
    posting.language = detect_language(text)
    if final_url != posting.apply_url and "linkedin.com" not in final_url:
        posting.apply_url = final_url[:1000]
    session.flush()
    return True
