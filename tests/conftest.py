from __future__ import annotations

import shutil
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from jobfinder import paths
from jobfinder.db.models import Base
from jobfinder.db.session import get_engine, reset_engine


@pytest.fixture
def home(monkeypatch, tmp_path: Path) -> Iterator[Path]:
    """Relocate JOBFINDER_HOME to a tmp dir with the default profile and an empty schema."""
    monkeypatch.setenv("JOBFINDER_HOME", str(tmp_path))
    paths.ensure_dirs()
    shutil.copy(paths.repo_root() / "tests" / "fixtures" / "profile.yaml", paths.config_path())
    reset_engine()
    Base.metadata.create_all(get_engine())
    yield tmp_path
    reset_engine()


@pytest.fixture
def db_session(home: Path) -> Iterator[Session]:
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Default suite must never touch the network; respx-mocked httpx calls never reach sockets."""
    if request.node.get_closest_marker("live"):
        yield
        return

    def _blocked(*args, **kwargs):
        raise RuntimeError("network access blocked in the default test suite")

    # Loopback stays open: on Windows, Python emulates socket.socketpair() with a real
    # 127.0.0.1 connect, and asyncio (hence Starlette's TestClient) needs one per loop.
    # Nothing under test listens locally, so a loopback dial can only be that emulation.
    orig_connect = socket.socket.connect

    def _connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else None
        if host in ("127.0.0.1", "::1"):
            return orig_connect(self, address, *args, **kwargs)
        _blocked()

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _connect)
    # DNS is a network call of its own: a test that leaks a real hostname would otherwise
    # stall on resolution before ever reaching connect().
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    yield
