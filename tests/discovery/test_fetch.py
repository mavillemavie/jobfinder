import httpx
import pytest
import respx

from jobfinder.discovery.base import SourceError
from jobfinder.discovery.fetch import (
    BudgetExceeded,
    HttpClient,
    RateLimited,
    extract_description,
    html_to_text,
    redact_url,
)


@respx.mock
def test_get_json_retries_on_500_then_succeeds() -> None:
    route = respx.get("https://api.test/x").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"ok": 1})]
    )
    client = HttpClient(retries=2)
    assert client.get_json("https://api.test/x") == {"ok": 1}
    assert route.call_count == 2 and client.calls_made == 2


@respx.mock
def test_429_raises_rate_limited_with_retry_after() -> None:
    respx.get("https://api.test/y").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "120"})
    )
    with pytest.raises(RateLimited) as exc:
        HttpClient().get("https://api.test/y")
    assert exc.value.retry_after == 120


@respx.mock
def test_budget_exceeded() -> None:
    respx.get("https://api.test/z").mock(return_value=httpx.Response(200, text="hi"))
    client = HttpClient()
    client.set_call_budget(1)
    assert client.get_text("https://api.test/z") == "hi"
    assert client.calls_remaining() == 0
    with pytest.raises(BudgetExceeded):
        client.get_text("https://api.test/z")


@respx.mock
def test_budget_gates_retries() -> None:
    route = respx.get("https://api.test/flaky").mock(
        return_value=httpx.Response(500)
    )
    client = HttpClient(retries=2)
    client.set_call_budget(1)
    with pytest.raises(BudgetExceeded):
        client.get("https://api.test/flaky")
    assert route.call_count == 1 and client.calls_made == 1


@respx.mock
def test_last_final_url_follows_redirect() -> None:
    respx.get("https://a.test/r").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://boards.greenhouse.io/acme/jobs/1"}
        )
    )
    respx.get("https://boards.greenhouse.io/acme/jobs/1").mock(
        return_value=httpx.Response(200, text="ok")
    )
    client = HttpClient()
    client.get_text("https://a.test/r")
    assert client.last_final_url == "https://boards.greenhouse.io/acme/jobs/1"


@respx.mock
def test_get_json_non_json_body_raises_source_error() -> None:
    respx.get("https://api.test/html").mock(
        return_value=httpx.Response(200, text="<html>oops</html>")
    )
    with pytest.raises(SourceError) as exc:
        HttpClient().get_json("https://api.test/html")
    assert "non-JSON body" in str(exc.value)


def test_html_to_text_strips_chrome() -> None:
    html = (
        "<html><head><style>x</style></head><body><nav>menu</nav><main>"
        "<h1>Data Analyst</h1><p>Build <b>dashboards</b>.</p><script>bad()"
        "</script></main><footer>f</footer></body></html>"
    )
    text = html_to_text(html)
    assert "Data Analyst" in text and "dashboards" in text
    assert "menu" not in text and "bad()" not in text and "f" != text.strip()[-1]


def test_extract_description_uses_domain_selector() -> None:
    html = (
        "<div class='nav'>junk</div>"
        "<div class='show-more-less-html__markup'>Role details here</div>"
    )
    result = extract_description(html, "https://www.linkedin.com/jobs/view/123")
    assert result == "Role details here"
    assert "junk" in extract_description(html, "https://unknown.example/x")


def test_redact_url_masks_jooble_key_in_path() -> None:
    assert redact_url("https://jooble.org/api/abc123SECRET") == "https://jooble.org/api/***"
    # only the segment right after /api/ is masked, and only on jooble.org
    assert redact_url("https://jooble.org/api/KEY/extra") == "https://jooble.org/api/***/extra"
    assert redact_url("https://example.test/api/notakey") == "https://example.test/api/notakey"


def test_redact_url_masks_query_secrets_but_keeps_app_id() -> None:
    red = redact_url(
        "https://api.adzuna.com/v1/api/jobs/ca/search/1"
        "?app_id=pubid&app_key=SUPERSECRET&what=data+analyst"
    )
    assert "SUPERSECRET" not in red
    assert "app_id=pubid" in red and "app_key=***" in red
    assert "what=data" in red
    # nothing secret → returned byte-identical
    plain = "https://api.test/x?a=1&b=2"
    assert redact_url(plain) == plain


@respx.mock
def test_401_from_jooble_does_not_leak_the_key() -> None:
    respx.post("https://jooble.org/api/SECRET").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    with pytest.raises(SourceError) as exc:
        HttpClient(retries=0).post_json("https://jooble.org/api/SECRET", {"keywords": "x"})
    assert "SECRET" not in str(exc.value)
    assert "https://jooble.org/api/***" in str(exc.value)


@respx.mock
def test_999_is_treated_as_rate_limited_with_hour_default() -> None:
    respx.get("https://www.linkedin.com/jobs-guest/x").mock(return_value=httpx.Response(999))
    with pytest.raises(RateLimited) as exc:
        HttpClient(retries=0).get("https://www.linkedin.com/jobs-guest/x")
    assert exc.value.retry_after == 3600 and exc.value.status == 999
    assert str(exc.value).startswith("999 from")


@respx.mock
def test_post_json_with_params_sends_query_params_and_no_body() -> None:
    route = respx.post(url__regex=r"https://api\.test/p.*").mock(
        return_value=httpx.Response(200, json={"ok": 1})
    )
    out = HttpClient().post_json_with_params(
        "https://api.test/p", params=[("a[]", "1"), ("a[]", "2"), ("b", "x")], headers={"X-K": "k"}
    )
    req = route.calls[0].request
    assert out == {"ok": 1} and req.url.params.get_list("a[]") == ["1", "2"]
    assert req.url.params["b"] == "x" and req.content == b"" and req.headers["X-K"] == "k"
