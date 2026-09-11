from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from jobfinder.discovery.base import SourceError

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36 jobfinder/0.1"
)

# CSS selectors for the description block, keyed by a substring of the host.
DESCRIPTION_SELECTORS: dict[str, str] = {
    "linkedin.com": ".show-more-less-html__markup, .description__text",
    "reed.co.uk": "[itemprop=description], .description",
    "jobbank.gc.ca": "#comments, .job-posting-detail-body, main",
    "greenhouse.io": "#content, .job__description, main",
    "lever.co": ".posting-page, .section-wrapper, main",
    "ashbyhq.com": "main",
    "workable.com": "[data-ui=job-description], main",
    "smartrecruiters.com": ".job-sections, main",
    "adzuna": ".adp-body, main",
}
DEFAULT_SELECTOR = "main, article, #job-description, .job-description, body"


# Query-string parameter names whose *value* is a credential. `app_id` is deliberately
# absent: it identifies the caller but is useless without its key, and keeping it visible
# makes adapter errors readable.
_SECRET_QS_KEYS = re.compile(
    r"(^|&)(app_key|api_key|apikey|key|token|secret|password)=[^&]*", re.IGNORECASE
)
_JOOBLE_KEY_IN_PATH = re.compile(r"(/api/)[^/]+")


def redact_url(url: str) -> str:
    """Mask credentials carried in a URL so they never reach logs, DB rows, or stdout.

    Two shapes are handled: Jooble puts the raw API key in the path
    (`https://jooble.org/api/<key>`), and several adapters pass keys as query params
    (`?app_id=...&app_key=...`). Anything else is returned unchanged.
    """
    try:
        parts = urlparse(url)
    except ValueError:  # pragma: no cover — urlparse is very permissive
        return url
    path = parts.path
    if "jooble.org" in parts.netloc.lower() and "/api/" in path:
        path = _JOOBLE_KEY_IN_PATH.sub(r"\1***", path, count=1)
    # Substituted on the raw query string rather than via parse_qsl/urlencode so every
    # other parameter survives byte-for-byte and the mask stays readable in a log line.
    query = _SECRET_QS_KEYS.sub(r"\1\2=***", parts.query)
    if path == parts.path and query == parts.query:
        return url
    return urlunparse(parts._replace(path=path, query=query))


class RateLimited(SourceError):
    def __init__(self, url: str, retry_after: int, status: int = 429) -> None:
        super().__init__(f"{status} from {redact_url(url)}; retry after {retry_after}s")
        self.retry_after = retry_after
        self.status = status


class BudgetExceeded(SourceError):
    pass


class HttpClient:
    def __init__(
        self, timeout: float = 30.0, retries: int = 2, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.8"},
            follow_redirects=True,
            transport=transport,
        )
        self.retries = retries
        self.calls_made = 0
        self._budget: int | None = None
        self.last_final_url: str | None = None

    def set_call_budget(self, n: int | None) -> None:
        self._budget = n
        self.calls_made = 0

    def calls_remaining(self) -> int | None:
        return None if self._budget is None else max(self._budget - self.calls_made, 0)

    def _check_budget(self, url: str) -> None:
        if self._budget is not None and self.calls_made >= self._budget:
            raise BudgetExceeded(
                f"call budget {self._budget} exhausted before {redact_url(url)}"
            )

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._check_budget(url)
            self.calls_made += 1
            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(min(2**attempt, 4))
                continue
            self.last_final_url = str(resp.url)
            # 999 is LinkedIn's undocumented "you are being throttled / blocked" status;
            # treat it exactly like 429, defaulting to a one-hour cooldown.
            if resp.status_code in (429, 999):
                retry_after = int(resp.headers.get("Retry-After", "3600") or 3600)
                raise RateLimited(url, retry_after, status=resp.status_code)
            if resp.status_code >= 500:
                last_exc = SourceError(f"{resp.status_code} from {redact_url(url)}")
                time.sleep(min(2**attempt, 4))
                continue
            if resp.status_code >= 400:
                raise SourceError(f"{resp.status_code} from {redact_url(url)}: {resp.text[:200]}")
            return resp
        detail = str(last_exc).replace(url, redact_url(url))
        raise SourceError(
            f"request to {redact_url(url)} failed after {self.retries + 1} attempts: {detail}"
        )

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> httpx.Response:
        return self._request("GET", url, params=params, headers=headers, auth=auth)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = self.get(url, **kwargs)
        try:
            return resp.json()
        except ValueError as e:
            raise SourceError(
                f"non-JSON body from {redact_url(url)}: {resp.text[:200]!r}"
            ) from e

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.get(url, **kwargs).text

    def post_json(self, url: str, json: Any, *, headers: dict[str, str] | None = None) -> Any:
        resp = self._request("POST", url, json=json, headers=headers)
        try:
            return resp.json()
        except ValueError as e:
            raise SourceError(
                f"non-JSON body from {redact_url(url)}: {resp.text[:200]!r}"
            ) from e

    def post_json_with_params(
        self,
        url: str,
        *,
        params: list[tuple[str, str]] | dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> Any:
        """POST with the filters in the query string and an empty body (Apollo's search API)."""
        resp = self._request("POST", url, params=params, headers=headers)
        try:
            return resp.json()
        except ValueError as e:
            raise SourceError(
                f"non-JSON body from {redact_url(url)}: {resp.text[:200]!r}"
            ) from e


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "svg", "form"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def extract_description(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    selector = next(
        (s for host, s in DESCRIPTION_SELECTORS.items() if host in url),
        DEFAULT_SELECTOR,
    )
    for sel in selector.split(","):
        node = soup.select_one(sel.strip())
        if node is not None and node.get_text(strip=True):
            return html_to_text(str(node))
    return html_to_text(html)
