# Contact-discovery playbook (v1, 2026-09)

What Plan 3 implements. Every step below names its provider, its exact endpoint (copied from
`api-notes.md`), its inputs/outputs, its credit cost, an expected hit rate **with its source**, and a
stop condition. Where neither `youtube-notes.md` nor `api-notes.md` supports a number, the entry reads
*unknown — measure in the 2-week trial* rather than a guess. Vendor self-promotion is flagged inline.

Sources cited as `(yt #N)` = `docs/research/youtube-notes.md` video N; `(api-notes: X)` =
`docs/research/api-notes.md` section X; `(spec §N)` = `docs/specs/2026-09-03-jobfinder-design.md`.

## Goal and floor

**Goal per match** (spec §11): a named hiring manager or recruiter with a verified email and/or a
direct phone.

**Floor per match:** a person's name **plus the company switchboard number whenever a phone is
discoverable at all**; failing that, the switchboard alone; failing *that*, a row recording what was
found, flagged for manual lookup (via `evidence.needs_manual`) (step 8 defines its exact shape) — a match is never dropped silently.
Both floor components come from free steps, so the floor never depends on a paid credit and a
budget-exhausted month still produces a callable row. Spec §18 criterion 2 sets the target: named
person with verified email or direct phone for ≥ 50 % of matches, a company phone for 100 %.

**Fixed constraints** (spec §2, §11): LinkedIn is never fetched — only `site:linkedin.com/in` web-search
results and the public data in their snippets. Nothing is auto-sent; the user sends every message himself.
Contact data lives only in the local SQLite DB and is deletable per record from the dashboard.

## Waterfall (implementable)

Steps run in order. `stop_when: verified_email_and_any_phone` (spec §6.1) ends the run early; each
provider also has a per-job cap `{apollo: 3, hunter: 2, websearch: 6}` (spec §6.1) enforced before the
call. Step 8 always runs, even after an early stop, so the floor is guaranteed.

| # | Step | Provider / exact endpoint | Input | Output | Credits / cost | Expected hit rate (source) | Stop condition |
|---|---|---|---|---|---|---|---|
| 1 | `posting_extract` | LLM fast (`LLM_PROVIDER`, local) — no HTTP | posting title + full text + apply URL | any person name / email / phone stated in the posting; ATS vendor + board token if the URL is an ATS URL | 0 credits (LLM cost outside the $50 contacts cap) | unknown — measure in the 2-week trial. Job Bank's `summary` blob carries `Employer:` but no person (api-notes: Job Bank Canada RSS) | always runs; a stated verified-looking email + any phone can satisfy `stop_when` immediately |
| 2 | `target_inference` | LLM fast — no HTTP | posting title, company, seniority, **posting language** | 2-3 likely hiring-manager titles + the talent-acquisition title, in the posting's language (FR and EN variants both emitted for FR postings) | 0 credits | n/a (always produces titles; title-correctness unknown — measure) | always runs; output feeds steps 4-6 |
| 3 | `company_resolve` | (a) posting/ATS metadata — 0 HTTP; (b) Serper `POST https://google.serper.dev/search`; (c) plain GET of the resolved homepage | company name, apply URL, ATS board token | domain, website, country, careers/ATS URL | (a) 0; (b) 1 websearch ($0 free tier / $0.001 paid); (c) 0 | (a) high for ATS-sourced postings — Lever `hostedUrl`, Ashby `jobUrl`, Workable `url`, Greenhouse `absolute_url` all carry the company slug (api-notes: Lever/Ashby/Workable/Greenhouse); (b) unknown — measure. No purpose-built company→domain API exists in scope (api-notes: Serper gotchas; yt open question 4) | stop at the first result whose domain appears in the apply URL **or** whose title matches the company name; never spend a second query |
| 4 | `people_search` | Apollo `POST https://api.apollo.io/api/v1/mixed_people/api_search` (params as **query params**), then Serper `POST /search` with `q=site:linkedin.com/in "<company>" "<title>"` | domain (step 3) + titles (step 2) | up to 3 candidate people: name, title, `has_email`, `has_direct_phone`, LinkedIn URL from search snippets | Apollo **0 credits** (api-notes: Apollo — People Search costs 0); **≤ 3 websearch** (spec §11 step 4's own allowance — one query per title from step 2, which emits 2-3, and both FR and EN variants on French postings) | Apollo: ~"50/50 chance" an email exists for a LinkedIn-visible person (yt #4 — Chrome-extension demo, single anecdote, not an API measurement); Apollo DB "210 M B2B contacts" (yt #5 — **vendor-promotional video**, unverified); search-snippet name extraction unknown — measure | stop at 3 candidates or when both providers return nothing; never fetch a LinkedIn page (spec §2) |
| 5 | `email_pattern` | Hunter `GET https://api.hunter.io/v2/domain-search?domain=…&limit=1` | domain | `pattern` (e.g. `{first}.{last}`) + 1 sample email with `confidence` and `verification.status`; **cached per domain in the DB** | **1 Hunter credit** (1 credit = 1 email returned — hence `limit=1`, not the default 10) (api-notes: Hunter). $0 on free tier; $0.0245 on Starter | Hunter demoed 98 % / 97 % confidence on 2 real lookups (yt #3, n=2 — not a hit rate). Pattern-availability rate unknown — measure | skip entirely if the domain's pattern is already cached, or if step 1/4 already produced an email; stop after 1 call |
| 6 | `email_resolve` | in order: (a) construct from cached pattern; (b) Hunter `GET /v2/email-verifier?email=…`; (c) Hunter `GET /v2/email-finder?domain=…&first_name=…&last_name=…`; (d) Apollo `POST /api/v1/people/match` (`reveal_personal_emails` left **false**) | person name + domain + pattern | email + status (`valid` / `accept_all` / `invalid` / `unknown`) + confidence | (a) 0; (b) **0.5 Hunter credit** each; (c) **1 Hunter credit**; (d) **1 Apollo credit** ("1 credit for demographics or email" — api-notes: Apollo). Per-job caps: 2 Hunter, 3 Apollo — so **at most 4 candidates are verified, and only 2 when step 5 already spent a credit on this job** | "80 % … vs 35-60 % for most providers" (yt #2 — **lemlist's own marketing claim, unverified**); real rate unknown — measure | stop on the first `status: valid`; `accept_all` never counts as verified (spec §11 confidence 0.4); stop when the 2-credit per-job Hunter budget is spent — 4 verifications on a cached-pattern domain, 2 after a fresh step-5 domain search |
| 7 | `phone` | (a) posting text (step 1); (b) Serper `POST /search` → `knowledgeGraph` phone; (c) Serper `q="<company>" contact phone` → `organic[].snippet`; (d) GET the site's `/contact` page | company name, domain | switchboard number (+ any direct number stated in the posting), country-formatted | (a) 0; (b) **0 queries — read off step 3's existing Serper response**, no second call; (c) **≤ 1 websearch** (spec §11 step 6's own allowance), and only when (a) and (b) both came back empty; (d) 0 | switchboard: unknown — measure; spec §18 sets the 100 % target and the floor makes it mandatory. Direct dial via Apollo is **out of scope in v1** — see Deviations D4. Apollo phone numbers are "typically the company's official number," not a personal cell (yt #4) | stop at the first plausible number matching the company's country; direct-dial found in the posting beats all of the above |
| 8 | `assemble` | none (local) | everything above | contact rows + confidence (spec §11 weights) + evidence trail + **floor check** | 0 | n/a | always runs last. If a switchboard was found but no person → a switchboard-only row (`confidence` 0.9 for the number, 0 for the person, per spec §11). If **neither** was found → one row with `role_kind="other"`, no name, no phone, the company website URL in the evidence trail, `confidence` 0.0 and `evidence.needs_manual = true` set, so the match still surfaces in the digest and dashboard as "manual lookup needed" |

**Per-job budget envelope at the caps:** ≤ 3 Apollo credits, ≤ 2 Hunter credits, and **5 of the 6
allowed web searches** (step 3 ×1 + step 4 ×3 + step 7 ×1), leaving 1 spare for a retry. Every number
here fits inside `config/profile.yaml`'s existing `per_job_credit_cap` — **no config change is
proposed by this playbook.**
**Expected typical path:** 1 Apollo credit, 1.5 Hunter credits, 3-4 web searches (step 3 ×1, step 4 ×2
of the 3 allowed, step 7 ×0-1).

**Satisfy the phone half of `stop_when` early.** Read literally, `verified_email_and_any_phone` could
only ever be satisfied after step 7 — the last step — so it would save nothing. Two free sources are
therefore harvested before then and written to the contact row as soon as they appear: any phone stated
in the posting (step 1) and the `knowledgeGraph` phone that comes back **on step 3's existing search
response** at no extra query. With "any phone" usually already in hand, `stop_when` can fire at the end
of step 6 and skip step 7's one paid search entirely. Step 7's (c) then runs only when no phone was
harvested early, and step 8 still always runs.

### Deviations from spec §11

Seven changes. The controller should amend spec §11's table before Plan 3 is written. **No numeric cap
changes:** the per-job web-search allocation is spec §11's own (step 3 ×1, step 4 ×3, step 7 ×1 = 5 of
the 6 allowed), and Hunter/Apollo stay inside `per_job_credit_cap` — nothing below asks for a
`config/profile.yaml` edit.

- **D1 — step 3 gains a free "domain from posting metadata" sub-step before any web search.**
  Reason: postings from Greenhouse/Lever/Ashby/Workable/SmartRecruiters carry the company slug in
  `absolute_url` / `hostedUrl` / `jobUrl` / `url` (api-notes for each), so the domain is often derivable
  at 0 cost. Saves one of the 6 per-job web searches on ATS-sourced matches.
- **D2 — Apollo people search is 0 credits, not "≤ 2 Apollo".** Reason: api-notes (Apollo) confirms
  `POST /mixed_people/api_search` **costs 0 credits and never returns an email or phone value — only
  booleans** (`has_email`, `has_direct_phone`, `last_name_obfuscated`). The spec's credit line for
  step 4 is wrong in both directions: search is free, and getting an actual value requires a separate
  chained `POST /people/match` enrichment call, which this playbook moves into steps 6 and 7.
- **D3 — spec step 5 is split into step 5 (pattern, per *domain*) and step 6 (resolution, per
  *person*).** Reason: Hunter charges **1 credit per email returned**, so a default `limit=10` Domain
  Search would burn 10 credits — a fifth of the monthly free tier — on one company. `limit=1` buys the
  `pattern` field for 1 credit, and the pattern is then reusable for every person at that domain and
  cacheable across months.
- **D4 — Apollo direct-dial is dropped from step 6 (phone) in v1.** Reason: three independent blockers
  in api-notes (Apollo): the phone reveal is **asynchronous and requires a public `webhook_url`**
  (jobfinder runs locally behind Tailscale — there is no public callback URL); it costs **8
  credits when a mobile is returned**, which alone exceeds the spec's own `per_job_credit_cap.apollo: 3`;
  and yt #4 reports the returned number is "typically the company's official number" anyway. The free
  `has_direct_phone` boolean from step 4 is still recorded as a signal (it tells the user a direct line exists
  and could be bought later), but no number is fetched. Revisit if a public callback endpoint ever exists.
- **D5 — step 7 adds Serper's `knowledgeGraph` block as a free switchboard source.** Reason: api-notes
  (Serper) lists `knowledgeGraph` in the response and marks it "unused by jobfinder"; Google's knowledge
  panel routinely carries a company's main phone number, and it costs **0 extra queries** because it
  arrives on the response step 3 already paid for — it is read, not fetched.
- **D6 — a new final step 8 `assemble` makes the floor an explicit step.** Reason: spec §11 states the
  floor as an outcome but assigns it to no step; making it a step gives Plan 3 a place to write the
  switchboard-only row and to define the one case spec §11 leaves undefined — no person *and* no phone
  — as a `needs_manual` row rather than silence. `needs_manual` is not a column; it is stored as `evidence["needs_manual"] = true` (with `evidence["reason"]` set to `"no person, no phone"`), and the dashboard reads it from there — no schema change.
- **D7 — SMTP RCPT is demoted from an equal alternative to a last-resort fallback.** Spec §11 step 5
  says "verified by Hunter verifier **or** SMTP RCPT check". This playbook makes Hunter's verifier
  primary and SMTP RCPT a fallback used only when Hunter credits are exhausted, capped at confidence
  0.6 and never allowed to report "verified". Reason: Hunter's verifier already performs the SMTP check
  and returns `accept_all`, `smtp_check`, `block` and `mx_records` for 0.5 credit (api-notes: Hunter);
  no source in either research doc describes a raw RCPT check at all (yt open question 5); and the host is
  on a residential connection where outbound port 25 is commonly blocked, so the check will often fail
  for reasons unrelated to the address.

Nothing else changes: steps 1, 2 and the general ordering (posting → titles → company → people → email
→ phone) are kept, and all six lead-gen videos independently converge on that same shape
(youtube-notes, Controller/analyst notes).

## Email construction + verification

**Pattern order.** Use Hunter's `pattern` from step 5 when present — it is observed, not guessed. Only
when Hunter has no pattern, construct candidates in this order and stop at the first `valid`:

1. `{first}.{last}@domain` 2. `{f}{last}@` 3. `{first}@` 4. `{first}{last}@`

**Cap at 4 candidates — set by the credit cap, not by taste.** Verification costs 0.5 Hunter credit
each and `per_job_credit_cap.hunter` is 2 (spec §6.1), so 4 verifications = exactly 2.0 credits and a
5th would breach the cap. The real per-job rule is therefore: **verify candidates in order until one
returns `valid` or the job's remaining Hunter budget is spent** — 4 verifications when step 5 read the
pattern from cache (0 credits spent), only 2 when step 5 just spent a credit on a fresh domain search.
yt #8 names `first.last` and `firstinitiallast` as the trial-and-error fallback; yt #1 demos a
permutation tool generating **34** candidates for one name+domain — jobfinder cannot verify 34 at 0.5
credit each (17 credits, a third of the monthly free tier), so the list is truncated to 4 and ordered
by the two patterns the sources actually name. Relative prevalence of the 4 patterns: unknown — measure
in the 2-week trial (log which pattern won per domain).

**Accents.** For French names, strip diacritics before constructing (`é`→`e`, `ç`→`c`) and try the
stripped form first. The accented form is a last resort that **competes for the same 4-slot budget** —
it displaces candidate 4, it never extends the list. Engineering rule, not a sourced figure.

**Catch-all detection.** Hunter `GET /v2/email-verifier` is the detector (api-notes: Hunter — this
answers youtube-notes open question 5; no video covered one). Treat the response as follows:

- `status: valid` + `smtp_check: true` → verified, confidence 0.85 (spec §11).
- `status: accept_all` **or** `accept_all: true` → **catch-all domain: flag it, never call it verified**,
  confidence 0.4 (spec §11). Cache the catch-all verdict per domain and skip further verification
  spend on that domain — every address there returns the same answer.
- `status: webmail` / `disposable` / `block` → discard; do not spend a second credit on that person.
- `status: unknown` → keep as a guess, confidence 0.25 (spec §11).

**When to trust SMTP RCPT (fallback only, per D7).** Procedure if used: MX lookup → connect :25 → EHLO
→ `MAIL FROM:<>` → `RCPT TO:<candidate>`. **Always probe a random non-existent local-part at the same
domain first** — if the random address also returns 250, the domain is catch-all and the real result is
meaningless. A 250 on the candidate and a rejection on the random probe caps confidence at 0.6 (spec
§11 "pattern + SMTP-valid"), never 0.85. Expect failures unrelated to the address: residential port-25
blocks, Microsoft 365/Google Workspace tarpitting and greylisting. Off by default; enable only after the
trial shows Hunter credits are the binding constraint.

**Which verifier:** Hunter, for three reasons — it is the only verifier in either research doc with a
documented price (0.5 credit), it returns the catch-all signal, and its rate limits (10 req/s, 300
req/min) are documented (api-notes: Hunter).

## Phone

**Direct-dial sources, in order:**

1. A number stated in the posting itself (step 1) — free, and the highest-confidence direct line there is.
2. Apollo `has_direct_phone` (`"Yes"` | `"Maybe: …"`) from the free people search — a **signal only**;
   no number is fetched in v1 (D4). Record it so the user can decide later whether a paid reveal is worth it.
3. Apollo `POST /people/match` with `reveal_phone_number=true` — **not implemented in v1**: async,
   requires a public `webhook_url`, and costs 8 credits vs. a per-job cap of 3 (api-notes: Apollo).

**Switchboard sources, in order:** Serper `knowledgeGraph` phone — **free, 0 extra queries**, read off
the response step 3 already paid for (D5) → `"<company>" contact phone` search snippet (**≤ 1 query per
job**, the whole of step 7's search budget, and only if the knowledge-graph block carried no number) →
a GET of the company's `/contact`, `/contact-us`, `/nous-joindre` page, parsing `tel:` hrefs first and
then E.164/NANP patterns → the ATS careers-page footer. Validate the country code against the posting's
country before accepting.

**How to phrase "ask for `<name>`"** — the switchboard is the floor, so the ask has to be short and
unremarkable. EN: *"Hi — could you put me through to `<name>` in `<team>`, please?"* If the name
is unknown: *"Hi — who looks after hiring for the `<title>` role? Could you put me through?"*
FR: *"Bonjour, pourriez-vous me transférer à `<name>`, `<titre>`, s'il vous plaît ?"* / *"Bonjour,
qui s'occupe de l'embauche pour le poste de `<titre>` ?"* The full 60-second call script is spec §13's
job, not this playbook's; this is only the line that gets past reception.

## Budget plan

**Cap:** `monthly_usd_cap: 50` (spec §6.1), hard. Free tiers first for a two-week measurement, then fund
the one provider that earned it.

**Volume assumption (stated, not sourced):** matches per day is **unknown until the trial** — spec §18
targets ≥ 5 relevant postings/day, of which some fraction score ≥ 70. The arithmetic below uses **3
matches/day = 90/month**, with 1/day and 5/day as the sensitivity bounds.

### Two-week free-tier trial

At 3 matches/day × 14 days = **42 matches**, at the *expected typical path* (3.5 searches, 1.5 Hunter
credits, 1 Apollo credit per match):

| Provider | Free grant (source) | Trial need | Verdict |
|---|---|---|---|
| Serper | **2,500 queries, one-time (not monthly)** (api-notes: Serper) | 42 × 3.5 = **147** (waterfall ceiling: 42 × 5 = 210) | fits with 94 % of the grant left |
| Hunter | **50 credits/month**, 1/email + 0.5/verification (api-notes: Hunter) | 42 × 1.5 = **63** | **over by 13** — rationing required (below) |
| Apollo | **not documented**; conflicting third-party figures ~75-100/mo vs 900-1,200/yr; fair-use ceiling 10,000/mo for non-paying accounts (api-notes: Apollo) | 42 × 1 = **42** enrichment credits (search itself is free) | fits if the low third-party figure is right; **measure** |
| LLM (steps 1-2) | `LLM_PROVIDER=claude_code` — the user's subscription | 42 × 2 calls | outside the $50 contacts cap; metered separately |

**Hunter rationing rule for the trial:** spend a Hunter credit only when (a) step 4 produced a named
person **and** (b) steps 1 and 6(d) produced no email. Hard-stop the month at **45 of 50** credits,
keeping 5 for retries. Cache `pattern` and the catch-all verdict per domain so repeat companies cost 0.

### Expected monthly counts at 90 matches/month

- **Serper:** 90 × 3.5 = **315 queries/month** (at the waterfall's 5-query ceiling: 450). The 2,500
  one-time grant lasts 2,500 ÷ 315 ≈ **7.9 months** (5.6 at the ceiling) — not a month-1 problem.
- **Hunter:** 90 × 1.5 = **135 credits/month** vs 50 free → **shortfall 85 credits/month**. This is the
  binding constraint of the whole waterfall.
- **Apollo:** 90 × 1 = **90 credits/month**, plus 90 free searches. Sits right on the disputed free-tier
  boundary — the single most important unknown to measure.

### The $50 allocation

**Recommended: Hunter Starter, $49/month, billed monthly — 2,000 credits/month** (api-notes: Hunter;
$49/mo, or $34/mo on an annual commitment). 2,000 credits is 14× the 135/month need, so it removes the
only structural shortfall outright, and Hunter is the one provider in this waterfall with a fully
documented price *and* credit schedule. **Take the monthly price, not the $34 annual one** — the annual
saving is $180 over 12 months, but the job search should end well before then, and $34/mo × 12 is a
$408 commitment against a $50/month cap.

At $49/month, **$1 of the cap remains**, so everything else must stay on its free tier: Serper on the
2,500-query grant with the DuckDuckGo HTML fallback behind it (spec §11 step 3), and Apollo free-tier
only. That is a feature, not a squeeze: v1 should hold **exactly one paid provider at a time**.

**Alternative: Serper Starter, $50 one-time for 50,000 queries, credits valid 6 months** (api-notes:
Serper — price is third-party-sourced, serper.dev/pricing does not render). Amortized $8.33/month, and
$0 in months 2-6. Choose this **instead** if the trial shows the bottleneck is *finding a named person*
(search-limited) rather than *resolving their email* (finder-limited), or if actual match volume lands
under ~33/month, where Hunter's 50 free credits ÷ 1.5 already covers every match.

**Not recommended in v1: Apollo Basic (~$49-59/mo, exact price unconfirmed — api-notes: Apollo).** The
email path it would buy is already covered by Hunter at a documented price, and the phone path it is
really wanted for is blocked by the webhook requirement (D4), not by credits.

**Decision rule at day 14.** Count `contact_runs` that ended without a verified email and classify the
blocking reason: `no_named_person` (→ search-limited → fund Serper) vs `hunter_credits_exhausted` /
`no_email_found` (→ finder-limited → fund Hunter). Fund the larger bucket; if neither exceeds its free
tier, **spend $0** and re-measure at day 30.

### `monthly_usd_cap` enforcement points

`budget.py` is the only path to a credit-spending call (spec §15) and checks the `spend_ledger` against
`monthly_usd_cap` **before** each of these, refusing when the cap is exceeded (dashboard shows "budget
exhausted", spec §11):

| Call | Unit cost, free tier | Unit cost, paid |
|---|---|---|
| Hunter `GET /v2/domain-search` (`limit=1`) | $0 (1 of 50 credits) | $49 ÷ 2,000 = **$0.0245** |
| Hunter `GET /v2/email-finder` | $0 (1 credit) | **$0.0245** |
| Hunter `GET /v2/email-verifier` | $0 (0.5 credit) | **$0.01225** |
| Apollo `POST /people/match` | $0 (1 credit) | **refuse — no confirmed $/credit** (api-notes: Apollo) |
| Serper `POST /search` | $0 (1 of 2,500) | $50 ÷ 50,000 = **$0.001** |
| Apollo `POST /mixed_people/api_search` | $0 (0 credits) | $0 — not ledgered, but still recorded in `contact_runs.steps` |

Two further rules: (1) a flat monthly subscription is posted to `spend_ledger` as a single charge on the
1st (e.g. $49 for Hunter Starter) so the remaining $1 naturally refuses every other paid call — that is
how "one paid provider at a time" is enforced mechanically rather than by policy; (2) any provider
whose $/unit is not confirmed (Apollo) is **free-tier-only**: `budget.py` refuses a paid call it cannot
price. Free tiers are also tracked as unit counters, not dollars, so exhaustion degrades the step
instead of breaching the cap.

## Provider API cheat-sheet for Plan 3

Copied from `api-notes.md` (verified 2026-09-04). Only the providers this waterfall uses.

**Apollo.io** — base `https://api.apollo.io/api/v1`, auth `x-api-key` header.
- People Search: `POST /mixed_people/api_search`. **Filters are query params, not a JSON body**:
  `person_titles[]`, `q_keywords`, `q_person_name`, `person_locations[]`, `person_seniorities[]`,
  `organization_locations[]`, `q_organization_domains_list[]`, `contact_email_status[]`, `page`,
  `per_page`. Response `people[]`: `id, first_name, last_name_obfuscated, title, has_email (bool),
  has_city/state/country (bool), has_direct_phone ("Yes"|"Maybe: …"), organization.name`. **0 credits.**
- People Enrichment: `POST /people/match`. Params `first_name`, `last_name`/`name`, `email`,
  `hashed_email`, `domain`, `organization_name`, `reveal_personal_emails` (default false),
  `reveal_phone_number` (default false, **requires `webhook_url`**, async). Response `person.email`,
  `person.phone_numbers[]`, `person.title`, `person.organization{…}`. **1 credit** for demographics or
  email; **+8** if a mobile is returned; 0 if nothing found.
- Rate limits: not documented. Pagination via `page`/`per_page`.
- **Gotcha that decides the design:** People Search never returns an email or phone *value* — Plan 3
  must chain Search → `/people/match` or it silently gets no contact data.

**Hunter.io** — base `https://api.hunter.io/v2/`, auth `api_key` query param, `X-API-KEY`, or Bearer.
- `GET /domain-search`: `domain`/`company`, `limit` (default 10, max 100 — **use 1**), `offset`, `type`,
  `seniority`, `department`. Response `domain`, `pattern`, `organization`,
  `emails[].value/type/confidence/first_name/last_name/position/seniority/department/decision_maker`,
  `emails[].verification.status`.
- `GET /email-finder`: domain/company + (`first_name`+`last_name` OR `full_name` OR `linkedin_handle`);
  optional `max_duration` (3-20 s). Response `email`, `score` (0-100), `domain`, `sources[]`,
  `verification.status`.
- `GET /email-verifier`: `email`. Response `status`
  (valid/invalid/accept_all/webmail/disposable/unknown), `result`, `score`, `regexp`, `gibberish`,
  `disposable`, `webmail`, `mx_records`, `smtp_server`, `smtp_check`, `accept_all`, `block`, `sources[]`.
- Rate limits: Domain Search & Finder **15 req/s, 500 req/min**; Verifier **10 req/s, 300 req/min**. Per
  key/IP; **403** on breach.
- Costs: 1 credit per email found (Domain Search *or* Finder); 0.5 credit per verification. Verification
  is a separate, billable call — it is **not** bundled with Finder.

**Serper.dev** — base `https://google.serper.dev`, auth `X-API-KEY` header.
- `POST /search`. Body `q`, `gl` (country), `hl` (language), `num`, `page`. Response `organic[].title,
  link, snippet, position`; also `knowledgeGraph` (used for switchboard, D5), `peopleAlsoAsk`,
  `relatedSearches` (unused).
- Free tier 2,500 one-time queries, no card. Rate limits third-party-sourced only (50 req/s Starter).
- Use `gl=ca&hl=fr` for French postings, `gl=ca&hl=en` / `gl=uk&hl=en` otherwise.

**Fallback web search (no key):** DuckDuckGo HTML (spec §11 step 3). Not researched in api-notes — its
result quality and rate limits are **unknown — measure in the 2-week trial**.

## Risks

- **Apollo ToS / data shape.** Last names are obfuscated until enrichment; enrichment is B2B personal
  data — PIPEDA/GDPR-relevant, so it stays in the local DB and is deletable per record (spec §11). Free
  tier is undocumented and six third-party trackers disagree (api-notes: Apollo) — the pipeline must
  degrade, not crash, when credits run out mid-month.
- **Hunter ToS.** Documented, priced, rate-limited; the only clean provider here. Risk is arithmetic,
  not legal: a careless `limit` on Domain Search burns 10 credits per call.
- **Serper.** A third-party proxy in front of Google, not Google itself; its own pricing page did not
  render on 2026-09-04, so every price and rate limit is third-party-sourced (api-notes: Serper). Treat a
  price change or a hard block as a live risk and keep the DuckDuckGo fallback wired.
- **LinkedIn.** Never fetched (spec §2). Only `site:linkedin.com/in` search results and the public data
  in their snippets are used. The LinkedIn guest *job-search* endpoint used in Plan 1 is unofficial and
  can block without notice (api-notes: LinkedIn guest search) — it must never become a contacts source.
- **Vendor claims stay flagged.** "80 % vs 35-60 %" (yt #2, lemlist's own channel), Apollo's "1
  credit/email, 5 credits/phone" and "~100 credits/month" (yt #5, Wiza-promotional, and **contradicted by
  Apollo's own docs**, which win), Hunter's "purges stale data every 6 months" and "117 M verified
  emails" (yt #5). None of these are load-bearing in the plan above.
- **Data freshness.** Any provider's contact data may be months stale; a person who left the company
  produces a `valid` email that reaches nobody. The confidence score is not a freshness score. Mitigation:
  prefer people the search snippet shows as *currently* at the company (yt #6's "currently at the
  company" filter is the same idea), and re-run discovery rather than trusting a cached person row.
- **Catch-all domains** silently defeat verification. Detected via Hunter's `accept_all` and capped at
  confidence 0.4 (spec §11); the verdict is cached per domain so the pipeline stops paying to learn the
  same thing twice.
- **French-language titles.** Query 7 of the YouTube pass (French) returned **zero** on-topic results —
  no French-market technique exists in the research (youtube-notes, open questions). Step 2 must emit FR
  *and* EN title variants for French postings ("responsable acquisition de talents", "gestionnaire
  embauche", "directeur/directrice"), and step 4 must run the `site:linkedin.com/in` query in both
  languages with `hl=fr`. Expected French hit rate: **unknown — measure in the 2-week trial**.
- **Compliance** (spec §11, plain language): the user sends each message themselves, one-to-one, about his own
  employment — outside CASL (Canada's anti-spam law, which targets commercial bulk electronic messages)
  and outside UK PECR bulk-marketing rules. Nothing is auto-sent. Contact data is local and deletable.
- **Outreach expectation-setting.** The only funnel numbers in the research are one practitioner's single
  cycle: 350 cold emails → 80 replies (~20 %) → 30 calls → 8 interviews → 1 offer, with a 3-touch
  follow-up at 1-2 week intervals (yt #8, n=1 anecdote). Useful for calibrating how many contacts a
  month needs to be worth $50; not a promise.

## Manual fallbacks for the user (not pipeline steps)

Listed because the research surfaced them and they work — but they are browser/UI tools and are
explicitly **out of the automated pipeline** (spec §2 constraints): Apollo, SignalHire or Kaspr Chrome
extensions on an open LinkedIn profile (yt #3, #4, #5 — SignalHire's free tier is stated inconsistently
in its own source, "8 credits" vs "10 people", yt #4); the Gmail-autocomplete trick, pasting candidate
addresses into a compose window and watching for a profile photo (yt #1, #2, #3); LinkedIn's own
"Contact info" panel (yt #1, #2); X/Twitter advanced search over bio and replies (yt #1, #2).
