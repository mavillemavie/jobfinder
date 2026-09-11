from jobfinder.app.security import client_allowed

CIDRS = ["127.0.0.0/8", "::1/128", "100.64.0.0/10"]


def test_client_allowed() -> None:
    assert client_allowed("127.0.0.1", CIDRS) and client_allowed("100.111.36.99", CIDRS)
    assert client_allowed("::1", CIDRS)
    assert not client_allowed("192.168.1.20", CIDRS) and not client_allowed("203.0.113.5", CIDRS)
    assert client_allowed("testclient", CIDRS)  # non-IP hosts only come from test clients


def test_healthz_and_base_page(client) -> None:
    assert client.get("/healthz").json() == {"ok": True}
    r = client.get("/")
    assert r.status_code == 200 and "jobfinder" in r.text and "htmx.min.js" in r.text


def test_favicon_is_silent(client) -> None:
    assert client.get("/favicon.ico").status_code == 204
