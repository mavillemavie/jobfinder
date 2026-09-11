from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from jobfinder import paths
from jobfinder.app.security import AllowedClientsMiddleware
from jobfinder.config import load_profile


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    """Start the cron scheduler with the server unless JOBFINDER_NO_SCHEDULER=1 (tests)."""
    sched = None
    if os.environ.get("JOBFINDER_NO_SCHEDULER") != "1":
        from jobfinder.app.scheduler import build_scheduler
        from jobfinder.db.hygiene import close_stale_runs
        from jobfinder.db.session import session_scope

        with session_scope() as session:
            close_stale_runs(session)
        sched = build_scheduler(load_profile())
        sched.start()
        app.state.scheduler = sched
    yield
    if sched is not None:
        sched.shutdown(wait=False)


def create_app(llm=None) -> FastAPI:  # noqa: ANN001
    paths.ensure_dirs()
    profile = load_profile()
    app = FastAPI(title="jobfinder", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.llm = llm
    app.add_middleware(AllowedClientsMiddleware, cidrs=profile.dashboard.allowed_client_cidrs)
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    from jobfinder.app.routes import brief, inbox, jobs, pipeline, runs, settings

    for router in (
        inbox.router, jobs.router, pipeline.router, settings.router, runs.router, brief.router,
    ):
        app.include_router(router)
    return app
