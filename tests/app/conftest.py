from __future__ import annotations

import warnings

import pytest

with warnings.catch_warnings():
    # starlette.testclient touches a deprecated anyio alias; not our code, keep -W error clean
    warnings.filterwarnings(
        "ignore", message="The anyio.abc.BlockingPortal alias is deprecated",
        category=DeprecationWarning,
    )
    from fastapi.testclient import TestClient

from jobfinder.app.main import create_app
from jobfinder.llm.fake import FakeLLM


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def client(home, fake_llm, monkeypatch) -> TestClient:
    monkeypatch.setenv("JOBFINDER_NO_SCHEDULER", "1")
    # Shortlist starts a contact-run thread in production; tests never spawn it (a test that
    # wants to observe the trigger monkeypatches its own recorder), and the in-flight set is
    # module-global, so it is cleared per test — posting ids repeat across fresh databases.
    from jobfinder.app import background

    background.CONTACTS_IN_FLIGHT.clear()
    monkeypatch.setattr("jobfinder.app.routes.inbox.start_contacts_thread", lambda pid, llm: None)
    app = create_app(llm=fake_llm)
    return TestClient(app)
