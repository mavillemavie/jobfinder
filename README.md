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

## What you need

- **Linux, macOS or Windows.** `run.sh` / `stop.sh` are Linux (they use `ss` and `xdg-open`),
  `run.ps1` / `stop.ps1` are Windows (PowerShell; if scripts are blocked, run once
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`); `uv run jobfinder serve` works everywhere,
  macOS included. The test suite runs on both Linux and Windows in CI.
- **[uv](https://docs.astral.sh/uv/)** — it installs Python 3.12+ and every dependency for you.
- **An LLM**, one of:
  - **Claude Code** (default, `LLM_PROVIDER=claude_code`): `npm install -g @anthropic-ai/claude-code`,
    then `claude` once to log in. Scoring, ingest, tailoring and drafts run through `claude -p` on your
    subscription; no API key.
  - **Anthropic API** (`LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`), pay per token.
  - **LM Studio** (`LLM_PROVIDER=lmstudio`), a local model; quality of scoring and tailoring drops.
- **A Gmail App Password** for the morning email (two minutes, `docs/SETUP-KEYS.md`). Without it the
  dashboard still shows everything; only the email is missing.
- **Optional, all free tiers:** job-board keys (Adzuna, Reed, JSearch, Jooble, the two Fantastic Jobs
  feeds via RapidAPI) and contact keys (Hunter, Serper, Apollo). The pipeline runs without any of
  them on the keyless sources (Job Bank, Remotive, WeWorkRemotely, employer ATS boards); each key
  adds coverage. `docs/SETUP-KEYS.md` has sign-up links, free-tier sizes and the order of value.

## Quickstart: from clone to first brief

    git clone https://github.com/mavillemavie/jobfinder.git && cd jobfinder
    uv sync                                   # Python + dependencies, nothing global
    cp .env.example .env                      # then paste your keys (see docs/SETUP-KEYS.md)
    uv run jobfinder db upgrade               # creates data/jobfinder.db

**1. Tell it who you are.** The first command that needs it copies `config/profile.example.yaml` to
`config/profile.yaml` (yours, gitignored). Edit it by hand now or from the dashboard later:

- `titles.clusters` and `titles.search_keywords` — the roles you want, in the words job boards use.
- `locations` — in priority order; a posting passes if any enabled entry matches.
- `scoring.candidate_note` — one paragraph the scorer reads about your situation (where you can
  work, languages, anything a posting might wrongly penalise you for).
- `scan.run_at`, `digest.send_at`, timezone.
- `documents.file_name_pattern` — e.g. `"Jane-Doe-{kind}-{company}"`.

**2. Give it your résumé.** Drop your résumé and cover letter as `.docx` into `input/`, then:

    uv run jobfinder ingest --resume <your-resume.docx> --cover <your-cover-letter.docx>

This is one strong-tier LLM call that turns them into `master/resume.yaml` and
`master/cover-letter.md` — the *master*: the only source of truth for every tailored document (the
tailor may never claim anything that is not in it). Open both files and fix anything the parse got
wrong; you can also edit them any time from the Settings page.

**3. Check the wiring.**

    uv run jobfinder doctor

Green on `database` and `claude CLI` (or your provider) is the requirement; missing keys are
listed with the doc to read. The last line says whether the dashboard port is free.

**4. First scan.** Start with discovery only to see what the boards return for your profile:

    uv run jobfinder scan --no-llm
    uv run jobfinder postings --status new --limit 20
    uv run jobfinder postings --status prefiltered_out --limit 20   # why things were dropped

Tune `titles`, `locations` and `scoring.min_keyword_overlap` until the `new` list looks like your
market, then run the full pipeline (discovery → hydration → LLM scoring → contact discovery for
matches; 20–40 minutes the first time, capped by `scoring.max_llm_scored_per_run`):

    uv run jobfinder scan

**5. Look at it.**

    ./run.sh            # Linux: dashboard + scheduler in the background, opens the browser
    .\run.ps1           # Windows (PowerShell), same thing
    uv run jobfinder serve            # any OS, foreground — http://localhost:3838

Inbox shows every match sorted by fit. From a Job page: *Generate docs* (tailored résumé + cover
letter, .docx and .pdf, with a truth check against your master), *Find contact*, *Write drafts*,
*Compose in Gmail*.

**6. Get the brief.** With `GMAIL_*` set:

    uv run jobfinder digest --dry-run         # prints what would be sent
    uv run jobfinder digest                   # sends it

From here on it runs itself: while `serve` (or `run.sh`) is up, the in-process scheduler runs the scan
at `scan.run_at` and sends the digest at `digest.send_at` (both in the profile's timezone). If the
machine was offline at either time, an hourly catch-up job runs them once it is back. For always-on
use, install the systemd unit (below) so it survives reboots.

## Daily use

- **Morning:** read the digest. Each match links to its Job page.
- **Job page:** *Generate docs* → download the .docx/.pdf → apply on the posting site → *Write drafts*
  → *Compose in Gmail* (opens a prefilled tab; you press send) → log the call or email under
  *Activity* → move the stage. *Find contact* re-runs contact discovery; *Re-score* re-runs the LLM fit.
- **Inbox:** every match, sorted by fit; filter by score, location, title; Shortlist / Dismiss / Re-score
  per row. Shortlist starts contact discovery for that posting.
- **Pipeline:** the board (`new → shortlisted → docs_ready → applied → contacted → interviewing →
  offer → closed`). Logging a call or email on an `applied` job moves it to `contacted` automatically.
  Closing a job as `rejected` also dismisses its same-title twins from that company.
- **Settings:** title clusters, exclusions, seniority, locations, scoring knobs, sources, budget cap,
  schedule, master résumé + cover letter editors, adapter health, month-to-date spend, *Run scan now*,
  *Send digest now*.
- **Runs:** every scan and digest with its stats and adapter errors.

The dashboard binds `dashboard.bind_host` (default loopback; `0.0.0.0` to reach it from a phone over
Tailscale) and only answers clients in `dashboard.allowed_client_cidrs` (loopback + Tailscale
`100.64.0.0/10`); everything else gets 403. Set `dashboard.public_base_url` to what the digest links
should point at.

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

**Linux (systemd user unit):**

    mkdir -p ~/.config/systemd/user
    cp docs/systemd/jobfinder.service ~/.config/systemd/user/   # then edit WorkingDirectory + PATH
    systemctl --user daemon-reload
    systemctl --user enable --now jobfinder
    loginctl enable-linger $USER      # keep it running without a login session

Logs: `journalctl --user -u jobfinder -f` and `data/logs/jobfinder.log` (rotating, 5 × 5 MB).

The unit pins `PATH` to include the directory where the `claude` CLI lives (under nvm if you installed
Node that way); if you change Node versions, update that line and `systemctl --user daemon-reload`.
If the machine is offline at scan or digest time, an hourly catch-up job runs them once it is back.

**Windows (Task Scheduler), from an elevated PowerShell in the repo folder:**

    $act = New-ScheduledTaskAction -Execute "uv" -Argument "run jobfinder serve" -WorkingDirectory (Get-Location)
    $trg = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName "jobfinder" -Action $act -Trigger $trg -Description "jobfinder dashboard + scheduler"

`uv` must be on the PATH the task sees (the default installer puts it there). Stop it with
`Stop-ScheduledTask jobfinder`, remove it with `Unregister-ScheduledTask jobfinder`. Logs go to
`data\logs\jobfinder.log`. The `claude` CLI installs with `npm install -g @anthropic-ai/claude-code`
and needs Git for Windows; run `claude` once from a terminal to log in.

**macOS:** a `launchd` agent with the same command, or just leave `./run.sh` running.

## How it works

- **Discovery** (`jobfinder.discovery`): keyless adapters (Job Bank RSS, Remotive, WeWorkRemotely,
  employer ATS boards; a LinkedIn guest-page adapter exists but is off by default) plus keyed ones
  (Adzuna, Reed, Jooble, JSearch, Fantastic Jobs' LinkedIn index and Active Jobs DB) behind
  per-adapter daily caps and cooldowns; postings are normalised, deduped, prefiltered on title cluster / exclusions / seniority /
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
