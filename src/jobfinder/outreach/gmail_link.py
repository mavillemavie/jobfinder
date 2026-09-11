from __future__ import annotations

from urllib.parse import quote


def gmail_compose_url(to: str | None, subject: str, body: str) -> str:
    params = [("view", "cm"), ("fs", "1")]
    if to:
        params.append(("to", to))
    params += [("su", subject), ("body", body)]
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params)
    return "https://mail.google.com/mail/?" + query
