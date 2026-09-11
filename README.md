# jobfinder

A personal job-search machine that runs on your own computer. Every morning it discovers postings
that fit your title clusters across your chosen locations, scores them against your master résumé
with an LLM, finds the hiring manager's contact inside a fixed credit budget, writes truth-bound
ATS-clean documents and outreach drafts, and reports by email with links into a local dashboard.
Nothing is ever sent on your behalf: you press Send on every application and every message.

Built for one person's search (data / BI / people-analytics roles in Canada and the UK) and shipped
as-is so others can adapt it: everything about *you* lives in `config/profile.yaml`, `master/`
and `.env`, none of which are tracked. Research notes on the job-board and contact APIs are in
`docs/research/`; keys and free tiers in `docs/SETUP-KEYS.md`.

## Daily use

- **Morning:** read the digest (07:00 America/Montreal). Each match links to its Job page.
- **Job page:** *Generate docs* → download the .docx/.pdf → apply on the posting site → *Write drafts*
  → *Compose in Gmail* (opens a prefilled tab; you press send) → log the call or email under
  *Activity* → move the stage. *Find contact* re-runs contact discovery; *Re-score* re-runs the LLM fit.
- **Inbox:** every match, sorted by fit; filter by score, location, title; Shortlist / Dismiss / Re-score
  per row.
- **Pipeline:** the board (`new → shortlisted → docs_ready → applied → contacted → interviewing →
  offer → closed`). Logging a call or email on an `applied` job moves it to `contacted` automatically.
- **Settings:** title clusters, exclusions, seniority, locations, scoring knobs, sources, budget cap,
  schedule, master résumé + cover letter editors, adapter health, month-to-date spend, *Run scan now*,
  *Send digest now*.
- **Runs:** every scan and digest with its stats and adapter errors.

## Setup (once)

    uv sync
    cp .env.example .env              # fill keys per docs/SETUP-KEYS.md (all optional except Gmail for the digest)
    # config/profile.yaml is created from config/profile.example.yaml on first run — edit it
    # (titles, locations, schedule, candidate note) by hand or later from the Settings page
    # drop your résumé + cover letter .docx into input/
    uv run jobfinder ingest --resume <file.docx>   # explicit file when input/ holds several
    uv run jobfinder doctor           # green on "database" is the only hard requirement
    ./run.sh                          # opens http://localhost:3838

The LLM defaults to your Claude subscription through the `claude` CLI (`LLM_PROVIDER=claude_code`),
so install [Claude Code](https://claude.com/claude-code) and log in once; `anthropic` (API key) and
`lmstudio` (local model) are the alternatives.

The dashboard binds `dashboard.bind_host` (default loopback; `0.0.0.0` to reach it from a phone over
Tailscale) and only answers clients in `dashboard.allowed_client_cidrs` (loopback + Tailscale
`100.64.0.0/10`); everything else gets 403.

## Commands

| Command | What it does |
|---|---|
| `uv run jobfinder version` | print the version |
| `uv run jobfinder db upgrade` | apply Alembic migrations (run.sh does this) |
| `uv run jobfinder ingest [--resume F] [--cover F]` | parse `input/*.docx` into `master/resume.yaml` + `master/cover-letter.md` (strong LLM tier) |
| `uv run jobfinder scan [--no-llm]` | discover → dedupe → prefilter → hydrate → LLM score → contact discovery for new matches |
| `uv run jobfinder postings [--status S] [--limit N]` | list postings |
| `uv run jobfinder rescore <id>` | re-run fit scoring for one posting, bypassing the LLM cache |
| `uv run jobfinder contacts <id> [--show]` | run the 8-step contact waterfall for one posting (or list stored contacts) |
| `uv run jobfinder tailor <id> [--lang fr]` | tailored résumé + cover letter as .docx and .pdf, ATS score, truth check |
| `uv run jobfinder documents <id>` | list generated documents |
| `uv run jobfinder digest [--dry-run]` | send (or print) the daily digest |
| `uv run jobfinder serve [--port] [--host]` | dashboard + in-process scheduler |
| `uv run jobfinder doctor` | health checks: DB, master files, `claude` CLI, keys, Gmail, port |
| `./run.sh [--no-open]` / `./stop.sh` | start (single instance) / stop the dashboard |

## Autostart

    mkdir -p ~/.config/systemd/user
    cp docs/systemd/jobfinder.service ~/.config/systemd/user/   # then edit WorkingDirectory + PATH
    systemctl --user daemon-reload
    systemctl --user enable --now jobfinder
    loginctl enable-linger $USER      # keep it running without a login session

Logs: `journalctl --user -u jobfinder -f` and `data/logs/jobfinder.log` (rotating, 5 × 5 MB).

The unit pins `PATH` to include the directory where the `claude` CLI lives (under nvm if you installed
Node that way); if you change Node versions, update that line and `systemctl --user daemon-reload`.
If the machine is offline at scan or digest time, an hourly catch-up job runs them once it is back.

## How it works

- **Discovery** (`jobfinder.discovery`): free adapters (Job Bank RSS, Remotive, WeWorkRemotely, LinkedIn
  guest search, ATS boards) plus keyed ones (Adzuna, Reed, Jooble, JSearch) behind per-adapter daily caps
  and cooldowns; postings are normalised, deduped, prefiltered on title cluster / exclusions / seniority /
  location / keyword overlap, and hydrated with the full text. Knobs: `titles`, `locations`,
  `scoring.max_posting_age_days`, `min_keyword_overlap`, `max_hydrate_per_run`, `sources.*`.
- **Scoring** (`jobfinder.scoring`): a compact résumé summary + the posting go to the fast LLM tier
  against a JSON schema; `fit_score ≥ scoring.match_threshold` (70) makes a *match*, creates a pipeline
  row, and triggers contact discovery. `scoring.max_llm_scored_per_run` caps LLM calls per scan.
- **Contacts** (`jobfinder.contacts`): the playbook's 8-step waterfall — posting extract, target titles,
  company domain, people search (Apollo free search + `site:linkedin.com/in` results, LinkedIn itself is
  never fetched), Hunter email pattern (cached per domain), email resolution (verify → finder → Apollo),
  phone (knowledge graph, `/contact` page, one search), assemble. `contacts.per_job_credit_cap`
  (`apollo 3, hunter 2, websearch 6`) and `contacts.monthly_usd_cap` (50) are enforced before every
  paid call; every match ends with at least a switchboard or a "manual lookup" row.
- **Tailoring** (`jobfinder.tailoring`): one strong-tier call rewrites the résumé and cover letter
  for the posting under a hard "never claim what the master doesn't" rule; a deterministic audit plus
  an LLM audit flag anything unsupported (`needs_review`); rendering is single-column Calibri 10.5 with
  real bullets; an ATS check scores structure + keyword coverage (`ready` needs ≥ 75 and a clean audit).
- **App** (`jobfinder.app`): FastAPI + HTMX dashboard, APScheduler cron jobs (`scan.run_at`,
  `digest.send_at`, both in America/Montreal), Gmail-SMTP digest built from everything since the last
  successful digest.

## Costs and limits

- **LLM:** your Claude subscription through the headless `claude -p` CLI (`LLM_PROVIDER=claude_code`,
  default). Fast tier = Sonnet (scoring, contact extraction), strong tier = Opus (ingest, tailoring,
  truth check, drafts, translation). Responses are cached by content hash under `data/llm-cache/`, so a
  re-run of the same input is free; `data/llm-cache/calls.jsonl` records every real call with tokens and
  seconds. Alternatives: `anthropic` (API key) or `lmstudio` (local).
- **Paid providers:** only Hunter has a confirmed price ($0.0245/credit on Starter); Apollo's is
  unconfirmed so paid Apollo calls are refused; Serper is $0.001/query after the 2,500 free grant. The
  `spend_ledger` table holds every unit; Settings shows month-to-date spend against `monthly_usd_cap`.
- **Free tiers used first:** Hunter 50 credits/month (the pipeline stops at 45), Serper 2,500 one-time.
  **Apollo's API requires a paid plan** (the Free plan returns 403 on every API call, verified 2026-09-05),
  so an Apollo key only helps once you subscribe. Without keys the waterfall falls back to DuckDuckGo HTML search, which
  challenges bots after a query or two — expect switchboard-only contacts until `SERPER_API_KEY` and
  `HUNTER_API_KEY` exist.

## Troubleshooting

- `uv run jobfinder doctor` first. Then `data/logs/jobfinder.log`, the **Runs** page (stats + adapter
  errors per run), and the adapter-health column on **Settings**.
- *No matches:* check `titles`/`locations` in Settings, then `min_keyword_overlap`; run
  `uv run jobfinder scan --no-llm` and `uv run jobfinder postings --status prefiltered_out` to see why.
- *"skipped_no_master":* `master/resume.yaml` is missing — run `ingest` (or paste YAML in Settings).
- *Adapter 401/429:* key missing or rate limit; the adapter cools down and the scan continues.
- *DuckDuckGo "bot challenge":* expected without a Serper key; the run still writes a floor contact row.
- *Documents `needs_review`:* open the truth-check findings on the Job page; the master, not the
  posting, is the source of truth — fix the master and regenerate.
- *Digest not arriving:* `GMAIL_USER`, `GMAIL_APP_PASSWORD` (a Google App Password, not the account
  password) and `DIGEST_TO` in `.env`; `uv run jobfinder digest --dry-run` shows what would be sent.

## Privacy

`config/profile.yaml`, `input/`, `master/`, `output/`, `data/` and `.env` are gitignored and never leave
your machine. The only
outbound traffic is to the LLM provider you configured, the job boards, and the contact APIs whose keys
you set; nothing is emailed except the digest to `DIGEST_TO`, and no application or message is ever sent
automatically. Contacts are stored locally and can be deleted per row from the Job page.

## Development

    uv run pytest -q                  # the suite never touches the network (fixtures under tests/fixtures/)
    uv run ruff check src tests
    uv run pytest -m live             # optional: real API calls, needs keys in .env

Every external service is an adapter with a fake; LLM calls go through `jobfinder.llm` with a JSON
schema. `CLAUDE.md` holds the conventions if you work on it with Claude Code.

## License

MIT — see `LICENSE`.
