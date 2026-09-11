from __future__ import annotations

import logging
import socket
from collections.abc import Callable

log = logging.getLogger(__name__)

# One board, one mail host, one public resolver-independent name: if none of these resolve,
# the machine is offline (2026-09-09: Wi-Fi never came back after a reboot and every source,
# scoring call and the digest failed with "Temporary failure in name resolution").
DEFAULT_HOSTS: tuple[str, ...] = ("api.adzuna.com", "smtp.gmail.com", "www.google.com")


def is_online(
    hosts: tuple[str, ...] = DEFAULT_HOSTS,
    resolver: Callable[[str, int], object] | None = None,
) -> bool:
    """True when at least one of `hosts` resolves. Any resolver error counts as offline."""
    # Looked up at call time, not bound as a default: the test suite's network guard
    # monkeypatches socket.getaddrinfo, and a bound default would slip past it.
    resolver = resolver or socket.getaddrinfo
    for host in hosts:
        try:
            resolver(host, 443)
            return True
        except Exception as exc:  # noqa: BLE001 — offline means offline, whatever the error
            log.debug("resolve %s failed: %s", host, exc)
    return False
