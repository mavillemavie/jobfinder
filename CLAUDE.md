# jobfinder — project instructions for Claude

A personal job-search machine (see README.md). The design spec (`docs/specs/`), plans
(`docs/plans/`) and build log (`docs/BUILD-LOG.md`) are private working notes: present in the
private repo, left out of the public one. When resuming, read `docs/BUILD-LOG.md` first if it exists.

## Commands
- `uv sync` — install. `uv run pytest -q` — tests (never network). `uv run ruff check src tests` — lint.
- `uv run jobfinder --help` — CLI. `uv run jobfinder db upgrade` — migrate. `uv run jobfinder ingest`.
- Live tests: `uv run pytest -m live` (need keys in `.env`).

## Rules
- PII: `config/profile.yaml input/ master/ output/ data/ .env` are gitignored. Never force-add. Never
  log résumé text. Never put a person's name, email or résumé content in docs or commit messages.
- Every external service is an adapter with a fake. Tests use fixtures under `tests/fixtures/<adapter>/`.
- LLM calls go through `jobfinder.llm` only, with a JSON schema, via `complete_json(task=..., ...)`.
  Never invent resume claims: tailoring prompts are bound to `master/`.
- Per-task commits; BUILD-LOG entry after each task with the pytest summary line.
- Paths: use `jobfinder.paths.*()` functions, never hardcode. `JOBFINDER_HOME` relocates everything (tests).

## Cross-plan interface contracts (later plans must conform)
- `jobfinder.llm.get_llm(settings) -> LLMProvider` (Plan 2 registry; Plan 0 ships `FakeLLM`, `ClaudeCodeProvider`).
- `LLMProvider.complete_json(*, task: str, system: str, user: str, schema: dict, tier: "fast"|"strong" = "fast", language: str = "en") -> dict`. Production call sites always pass `tier` explicitly; the default exists for tests.
- `jobfinder.tailoring.master_schema.load_master(lang: str = "en") -> MasterResume`; `MasterResume.skill_terms() -> set[str]`.
- `jobfinder.discovery.scan.run_scan(session, *, profile, settings, llm, no_llm: bool = False) -> Run` (Plan 1/2).
- `jobfinder.scoring.scorer.score_posting(session, posting, *, master, llm, profile) -> Score` (Plan 2).
- `jobfinder.contacts.waterfall.run_for_posting(session, posting_id: int, *, llm, settings, profile) -> ContactRun` (Plan 3).
- `jobfinder.tailoring.tailor.tailor_posting(session, posting_id: int, *, llm, profile) -> list[Document]` (Plan 4).
- `jobfinder.outreach.drafts.draft_for_posting(session, posting_id: int, *, llm, profile) -> list[Draft]` (Plan 5).
- `jobfinder.app.digest.send_digest(session, *, settings, profile) -> Run` (Plan 6).
