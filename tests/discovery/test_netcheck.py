from jobfinder.discovery.netcheck import is_online


def test_online_when_any_host_resolves() -> None:
    def resolver(host, port):
        if host == "b.test":
            return [("stub",)]
        raise OSError("Temporary failure in name resolution")

    assert is_online(hosts=("a.test", "b.test"), resolver=resolver) is True


def test_offline_when_every_host_fails() -> None:
    def resolver(host, port):
        raise OSError("Temporary failure in name resolution")

    assert is_online(hosts=("a.test", "b.test"), resolver=resolver) is False


def test_offline_on_unexpected_resolver_errors_too() -> None:
    def resolver(host, port):
        raise RuntimeError("network access blocked")

    assert is_online(hosts=("a.test",), resolver=resolver) is False


def test_default_resolver_is_getaddrinfo(monkeypatch) -> None:
    import socket

    seen = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p: seen.append(h) or [("x",)])
    assert is_online() is True
    assert seen and all(isinstance(h, str) for h in seen)
