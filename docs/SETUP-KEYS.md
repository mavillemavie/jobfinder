# Setup — API keys and app password

Create each free account, then paste the value into `.env` (copy `.env.example` first:
`cp .env.example .env`). **The build and tests never need these** — adapters are built and tested
against recorded fixtures, and live smoke tests skip themselves when a key is absent (spec §16). The
live pipeline degrades gracefully without any single one; the "Without it" column says how.

Free-tier figures come from `docs/research/api-notes.md` (verified 2026-09-04). "not documented" means
the vendor publishes no number on any reachable page — it is not an omission here.

| `.env` key | Service | Sign-up | Free tier | Used by | Without it |
|---|---|---|---|---|---|
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | Adzuna | https://developer.adzuna.com/signup | **not documented** — registration required to see it | discovery (CA + UK postings) | fewer CA/UK postings; other sources still run |
| `REED_API_KEY` | Reed | https://www.reed.co.uk/developers | free; **no paid tier documented at all**. Third-party (unverified): ~1,000 req/day/key | discovery (UK postings) | no UK Reed postings; Adzuna still covers UK |
| `JOOBLE_API_KEY` | Jooble | https://jooble.org/api/about (form; key is approved manually) | **500 requests total — a lifetime cap, not monthly** | discovery (aggregator) | one aggregator fewer; small loss, and the lifetime cap makes it low-value anyway |
| `RAPIDAPI_KEY` | JSearch, via RapidAPI | https://rapidapi.com → search "JSearch" (their page is a JS app; api-notes used the `openwebninja.com/api/jsearch` mirror for docs) | 200 requests/month, 1,000/hr (mirror-sourced, not RapidAPI's own page) | discovery (aggregated Google-for-Jobs data) | no JSearch results; capped at 6 calls/day by config anyway |
| `APOLLO_API_KEY` | Apollo.io — **API needs a paid plan** (verified 2026-09-05: the Free plan answers `403 … not included in your Free plan`, even with a master key; the pipeline records the error and continues, so leave this empty unless you subscribe) | https://www.apollo.io → sign up free; key under Settings → Integrations → API | **not documented.** Fair-use ceiling "10,000 credits/account/month for non-paying accounts"; six third-party trackers disagree (~75-100/mo vs 900-1,200/yr). People **Search costs 0 credits**; enrichment costs 1 | contacts step 4 (people search) and step 6 (email enrichment) | no people search — names come only from `site:linkedin.com/in` web results; email hit rate drops |
| `HUNTER_API_KEY` | Hunter.io | https://hunter.io → sign up free; key under Dashboard → API Keys | **50 credits/month** (1 credit = 1 email found, 0.5 = 1 verification) | contacts steps 5-6 (domain pattern, email finder, verifier) | **the biggest single loss** — no email pattern and no catch-all detection; emails stay unverified guesses (confidence ≤ 0.25-0.6, spec §11) |
| `SERPER_API_KEY` | Serper.dev | https://serper.dev → sign up (no credit card) | **2,500 queries, one-time — not monthly** | contacts steps 3, 4, 7 (company domain, people search, switchboard phone) | falls back to DuckDuckGo HTML scraping — works, quality unmeasured |
| `GMAIL_USER` / `GMAIL_APP_PASSWORD` / `DIGEST_TO` | Gmail SMTP (`smtp.gmail.com:587`) | https://myaccount.google.com/apppasswords (see below) | free | the 07:00 daily digest email | no digest arrives; the dashboard at `http://localhost:3838` still shows everything |

Also in `.env.example` but **not sign-up keys**: `LLM_PROVIDER` (defaults to `claude_code`, which uses
your existing Claude Code login — no key), `ANTHROPIC_API_KEY` (only if `LLM_PROVIDER=anthropic`), and
`LMSTUDIO_BASE_URL` (only if `LLM_PROVIDER=lmstudio`; defaults to `http://localhost:1234`).

## Gmail App Password (digest)

1. Google Account → **Security** → **2-Step Verification** must be **on** (app passwords do not exist
   as an option until it is).
2. **Security** → **App passwords** (https://myaccount.google.com/apppasswords) → create one named
   `jobfinder` → Google shows a 16-character string → paste it as `GMAIL_APP_PASSWORD`
   (spaces optional, they are ignored).
3. `GMAIL_USER` = the Gmail address the digest is sent **from**; `DIGEST_TO` = where it should land
   (they can be the same address).

The app password is not your Google password, it is revocable on its own, and it grants SMTP send only.
Revoke it from the same page if the machine is ever compromised.

## Order of value

If you only create some of these, create them in this order:

1. **`ADZUNA_APP_ID`/`ADZUNA_APP_KEY` + `REED_API_KEY`** — the most postings per key, and discovery is
   what the whole pipeline runs on. Nothing downstream matters without postings.
2. **`HUNTER_API_KEY`** — the one contact provider with a documented free tier, a documented price, and
   the catch-all detector. It is the binding constraint on the contacts waterfall.
3. **`SERPER_API_KEY`** — 2,500 free queries covers roughly the first 6-8 months of contact discovery
   at expected volume; the free fallback is noticeably weaker.
4. **`APOLLO_API_KEY`** — free people search (0 credits) is genuinely useful; the enrichment free tier
   is undocumented, so treat whatever it gives as a bonus.
5. **`JOOBLE_API_KEY`** — 500 lifetime requests, so it is a one-off top-up of coverage, not a source.
6. **`RAPIDAPI_KEY`** (JSearch) — 200 requests/month, capped to 6 calls/day in `config/profile.yaml`.

`GMAIL_*` sits outside this order: it costs nothing, takes two minutes, and is the only thing standing
between you and the 07:00 digest. Do it whenever.

**Spend:** every paid call goes through `budget.py` against `contacts.monthly_usd_cap: 50`. On free
tiers alone the pipeline spends **$0**. See `docs/research/contact-discovery-playbook.md` → Budget plan
for the two-week measurement and the $50 recommendation that follows it.
