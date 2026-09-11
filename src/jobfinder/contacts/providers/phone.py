from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jobfinder.discovery.base import SourceError
from jobfinder.discovery.fetch import BudgetExceeded, HttpClient, RateLimited

_NANP = re.compile(
    r"(?:\+?1[\s.-]*)?\(?([2-9]\d{2})\)?[\s.-]*([2-9]\d{2})[\s.-]*(\d{4})(?!\d)"
)
_UK = re.compile(r"(?:\+44\s?\(?0?\)?|0)(\d[\d\s]{8,11}\d)(?!\d)")
CONTACT_PATHS = ("/contact", "/contact-us", "/contactez-nous", "/nous-joindre", "/")


def normalize_phone(raw: str | None, country: str) -> str | None:
    if not raw:
        return None
    country = (country or "CA").upper()
    if country in ("CA", "US"):
        m = _NANP.search(raw)
        if not m:
            return None
        return f"+1 {m.group(1)}-{m.group(2)}-{m.group(3)}"
    if country == "GB":
        m = _UK.search(raw)
        if not m:
            return None
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) not in (9, 10):
            return None
        if len(digits) == 10 and digits.startswith("20"):
            return f"+44 {digits[:2]} {digits[2:6]} {digits[6:]}"
        if len(digits) == 10:
            return f"+44 {digits[:3]} {digits[3:6]} {digits[6:]}"
        return f"+44 {digits[:2]} {digits[2:5]} {digits[5:]}"
    return None


def phones_in_text(text: str, country: str) -> list[str]:
    pattern = _NANP if (country or "CA").upper() in ("CA", "US") else _UK
    out: list[str] = []
    for m in pattern.finditer(text or ""):
        n = normalize_phone(m.group(0), country)
        if n and n not in out:
            out.append(n)
    return out


def phone_from_html(html: str, country: str) -> str | None:
    soup = BeautifulSoup(html or "", "lxml")
    for a in soup.select('a[href^="tel:"]'):
        n = normalize_phone(a["href"][4:], country)
        if n:
            return n
    found = phones_in_text(soup.get_text(" ", strip=True), country)
    return found[0] if found else None


def fetch_contact_page_phone(
    http: HttpClient, website: str, country: str
) -> tuple[str, str] | None:
    if not website:
        return None
    base = website if website.startswith("http") else f"https://{website}"
    for path in CONTACT_PATHS:
        if path == "/":
            url = base.rstrip("/") + "/"
        else:
            url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
        try:
            html = http.get_text(url)
        except (RateLimited, BudgetExceeded):
            raise
        except SourceError:
            continue
        n = phone_from_html(html, country)
        if n:
            return n, url
    return None
