from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from jobfinder.contacts.budget import Budget
from jobfinder.discovery.base import SourceError
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient, RateLimited

log = logging.getLogger(__name__)
SERPER_URL = "https://google.serper.dev/search"
DDG_URL = "https://html.duckduckgo.com/html/"
# DuckDuckGo answers a bot challenge with HTTP 200 and no results; these strings identify it.
_DDG_CHALLENGE_MARKERS = ("anomaly-modal", 'id="challenge-form"')
DDG_CHALLENGE_RETRY_S = 3600
_sleep = time.sleep  # injectable for tests


@dataclass
class SearchHit:
    title: str
    link: str
    snippet: str


@dataclass
class SearchResult:
    hits: list[SearchHit]
    knowledge_graph: dict[str, Any] = field(default_factory=dict)
    backend: str = "serper"
    query: str = ""


class SerperClient:
    name = "serper"

    def __init__(self, http: HttpClient, api_key: str) -> None:
        self.http, self.api_key = http, api_key

    def search(self, q: str, *, gl: str = "ca", hl: str = "en", num: int = 10) -> SearchResult:
        payload = self.http.post_json(
            SERPER_URL,
            {"q": q, "gl": gl, "hl": hl, "num": num},
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
        )
        hits = [
            SearchHit(str(o.get("title", "")), str(o.get("link", "")), str(o.get("snippet", "")))
            for o in payload.get("organic", [])
            if o.get("link")
        ]
        return SearchResult(hits, payload.get("knowledgeGraph") or {}, "serper", q)


def _unwrap_ddg(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    return href


class DuckDuckGoClient:
    name = "ddg"

    def __init__(self, http: HttpClient, pause_s: float = 1.0) -> None:
        self.http = http
        self.pause_s = pause_s
        self._last_request: float | None = None

    def search(self, q: str, *, gl: str = "ca", hl: str = "en", num: int = 10) -> SearchResult:
        # Consecutive rapid requests trip DuckDuckGo's bot challenge; pace them.
        if self._last_request is not None and self.pause_s > 0:
            _sleep(self.pause_s)
        self._last_request = time.monotonic()
        html = self.http.get_text(DDG_URL, params={"q": q, "kl": f"{gl}-{hl}"})
        soup = BeautifulSoup(html, "lxml")
        results = soup.select("div.result")
        if not results and any(m in html for m in _DDG_CHALLENGE_MARKERS):
            raise RateLimited(
                f"{DDG_URL} (bot challenge page)", DDG_CHALLENGE_RETRY_S, status=200
            )
        hits: list[SearchHit] = []
        for res in results[:num]:
            a = res.select_one("a.result__a")
            if a is None or not a.get("href"):
                continue
            snippet_el = res.select_one(".result__snippet")
            hits.append(
                SearchHit(
                    a.get_text(" ", strip=True),
                    _unwrap_ddg(a["href"]),
                    snippet_el.get_text(" ", strip=True) if snippet_el else "",
                )
            )
        return SearchResult(hits, {}, "ddg", q)


class WebSearch:
    """Budget-checked search: Serper when allowed, DuckDuckGo as fallback, None when neither."""

    def __init__(
        self, serper: SerperClient | None, ddg: DuckDuckGoClient | None, budget: Budget
    ) -> None:
        self.serper, self.ddg, self.budget = serper, ddg, budget
        self.last_reason = ""
        self._dead: set[str] = set()  # backends that rate-limited us during this run

    def search(
        self, q: str, *, gl: str = "ca", hl: str = "en", num: int = 10
    ) -> SearchResult | None:
        reasons: list[str] = []
        for backend, client in (("serper", self.serper), ("ddg", self.ddg)):
            if client is None:
                reasons.append(f"{backend}: not configured")
                continue
            if backend in self._dead:
                reasons.append(f"{backend}: unavailable for this run (rate limited)")
                continue
            ok, why = self.budget.can_spend("websearch", 1.0, backend=backend)
            if not ok:
                reasons.append(f"{backend}: {why}")
                if "per-job cap" in why:
                    break
                continue
            try:
                result = client.search(q, gl=gl, hl=hl, num=num)
            except RateLimited as exc:
                self._dead.add(backend)
                reasons.append(f"{backend}: {exc}")
                log.warning("web search via %s rate limited for this run: %s", backend, exc)
                continue
            except (BudgetExceeded, SourceError) as exc:
                reasons.append(f"{backend}: {exc}")
                log.warning("web search via %s failed: %s", backend, exc)
                continue
            self.budget.spend("websearch", 1.0, backend=backend)
            self.last_reason = "; ".join(reasons) if reasons else "ok"
            return result
        if self.serper is None and self.ddg is None:
            self.last_reason = "no search backend"
        else:
            self.last_reason = "; ".join(reasons) or "no search backend"
        return None
