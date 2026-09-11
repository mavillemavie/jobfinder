# API notes — external services for jobfinder

Research for Plan 1 (discovery adapters) and Plan 3 (contact adapters). "Verified on" names the actual
page/tool used that day. Numbers not confirmed on an official source are marked **not documented**,
with third-party corroboration noted and flagged as such — never presented as official. Sample
responses are trimmed to fields read; no scraped posting content is reproduced from the two live
probes (element/field names only), per the brief's rule.

Methodology per service: context7 first (no libraries indexed here — all plain REST/RSS APIs, not
SDK-documented), then WebFetch on official docs, then WebSearch only to locate a page or corroborate a
figure, then (Job Bank + LinkedIn only) live curl probes.

---

## Apollo.io
- Verified on: 2026-09-04 — via: WebFetch (docs.apollo.io/reference/people-api-search,
  /reference/people-enrichment, apollo.io/pricing) + WebSearch corroboration
- Base `https://api.apollo.io/api/v1`. Auth: `x-api-key` header (or OAuth2 bearer).
- **People Search** — `POST /mixed_people/api_search`. Filters are **query params, not JSON body**:
  `person_titles[]`, `q_keywords`, `q_person_name`, `person_locations[]`, `person_seniorities[]`,
  `organization_locations[]`, `q_organization_domains_list[]`, `contact_email_status[]`, `page`,
  `per_page`. Response `people[]`: `id, first_name, last_name_obfuscated, title, has_email (bool),
  has_city/state/country (bool), has_direct_phone ("Yes"|"Maybe: ..."), organization.name` +
  `organization.has_*` booleans. **Costs 0 credits.**
- **People Enrichment (match)** — `POST /people/match`. Params: `first_name`, `last_name`/`name`,
  `email`, `hashed_email`, `domain`, `organization_name`, `reveal_personal_emails` (bool, default
  false), `reveal_phone_number` (bool, default false, **requires `webhook_url`** — async). Response:
  `person.email`, `person.phone_numbers[]`, `person.title`, `person.organization{...}`.
- Free tier: **not documented.** apollo.io/pricing's FAQ (2026-09-04) states a fair-use ceiling of
  "10,000 credits/account/month for non-paying accounts" (not necessarily the granted amount); its
  plan-comparison table is JS-built and didn't render via WebFetch. Six third-party trackers
  (2026-09-04) disagree with each other: "~75-100/mo + 5 mobile + 10 export" vs. "900-1,200/yr."
  youtube-notes.md video #5 (2026-04-25, Wiza-promotional) claimed "~100/mo + 10 exports." Record all,
  trust none — answers youtube-notes open question 2 only partially.
- Entry paid: Basic plan named by third parties (~$49-59/mo), exact price unconfirmed.
- Rate limits: not documented. `page`/`per_page` paginate People Search.
- Gotchas: **People Search never returns an email or phone value** — only booleans; Plan 3 must chain
  Search → Enrichment (`/people/match`) or it silently returns no contact data. Phone reveal is async
  and needs `webhook_url` — not readable synchronously. Enrichment credit cost (its own doc page): "1
  credit for demographics or email, plus 8 credits if a mobile phone is returned" (0 if nothing found)
  — does NOT match youtube-notes video #5's "1 credit/email, 5 credits/phone" claim; docs win.
- Sample response (People Enrichment, fields read only):
  ```json
  {"person": {"email": "jane@example.com",
    "phone_numbers": [{"raw_number": "+1...", "type": "mobile"}],
    "title": "VP Engineering",
    "organization": {"name": "Example Inc", "website_url": "example.com"}}}
  ```

## Hunter.io
- Verified on: 2026-09-04 — via: WebFetch (hunter.io/api-documentation/v2, hunter.io/pricing) +
  WebSearch corroboration (help.hunter.io rate-limit article)
- Base `https://api.hunter.io/v2/`. Auth: `api_key` query param, `X-API-KEY`, or Bearer header.
- Domain Search — `GET /domain-search`: `domain`/`company`, `limit` (default 10, max 100), `offset`,
  `type`, `seniority`, `department`. Response: `domain`, `pattern`, `organization`,
  `emails[].value/type/confidence/first_name/last_name/position/seniority/department/decision_maker`,
  `emails[].verification.status`.
- Email Finder — `GET /email-finder`: domain/company + (`first_name`+`last_name` OR `full_name` OR
  `linkedin_handle`); optional `max_duration` (3-20s). Response: `email`, `score` (0-100), `domain`,
  `sources[]`, `verification.status`.
- Email Verifier — `GET /email-verifier`: `email`. Response: `status`
  (valid/invalid/accept_all/webmail/disposable/unknown), `result`, `score`, `regexp`, `gibberish`,
  `disposable`, `webmail`, `mx_records`, `smtp_server`, `smtp_check`, `accept_all`, `block`,
  `sources[]`. **This is Hunter's catch-all-domain detector** — answers youtube-notes open question 5
  (no video covered one; Hunter's Verifier does via `accept_all`).
- Free tier: **50 credits/month** (hunter.io/pricing, 2026-09-04). Costs: 1 credit = 1 email found
  (Domain Search or Finder); 0.5 credit = 1 verification. **Reconciles youtube-notes open question 1**:
  videos #3/#5's "25 searches + 50 verifications/month" (2026-04-25) is arithmetically the same 50
  credits (25×1 + 50×0.5) under this cost schedule — same allotment, different framing; current pricing
  page states "50 credits," treat as canonical.
- Entry paid: Starter $49/mo ($34/mo annual) for 2,000 credits/month.
- Rate limits: Domain Search & Finder 15 req/s / 500 req/min; Verifier 10 req/s / 300 req/min (docs
  page, corroborated by a help.hunter.io article via WebSearch). Per key/IP; 403 on breach.
- Gotchas: none — draft's params/fields all match documented shape. Verification is a separate,
  half-credit call, not bundled free with Finder/Search.
- Sample response (Domain Search, trimmed):
  ```json
  {"data": {"domain": "example.com", "pattern": "{first}.{last}",
    "emails": [{"value": "jane.doe@example.com", "type": "personal", "confidence": 92,
      "first_name": "Jane", "last_name": "Doe", "position": "VP Engineering",
      "verification": {"status": "valid"}}]}}
  ```

## Serper.dev
- Verified on: 2026-09-04 — via: WebFetch (serper.dev; serper.dev/pricing 404'd — pricing is
  third-party) + WebSearch
- Base `https://google.serper.dev`. Auth: `X-API-KEY` header.
- Web Search — `POST /search`. Body: `q`, `gl` (country), `hl` (language), `num`, `page`. Response:
  `organic[].title, link, snippet, position`; also `knowledgeGraph`, `peopleAlsoAsk`,
  `relatedSearches` (unused by jobfinder).
- Free tier: **2,500 one-time queries, no credit card** (serper.dev homepage, confirmed). Answers
  youtube-notes open question 7 — Serper is the practical stand-in for a web-search API since Google's
  own Custom Search JSON API wasn't in the brief's service table.
- Entry paid: **third-party only** (costbench.com/coldiq.com, 2026-09-04; not renderable on
  serper.dev/pricing) — Starter $50/50,000 ($1.00/1k), Standard $375/500k, Scale $1,250/2.5M, Ultimate
  $3,750/12.5M; credits valid 6 months.
- Rate limits: third-party only — 50 req/s (Starter) to 300 req/s (Ultimate).
- Gotchas: no dedicated company-name→domain resolver exists in scope (youtube-notes open question 4);
  Serper's web search is the candidate tool for that step (a `site:`/company-name query), not a
  purpose-built domain-lookup API — Plan 3 builds that logic on raw search results.
- Sample response (trimmed):
  ```json
  {"organic": [{"title": "...", "link": "https://...", "snippet": "...", "position": 1}]}
  ```

## Adzuna
- Verified on: 2026-09-04 — via: WebFetch + raw curl of developer.adzuna.com/docs/search + WebFetch
  of a third-party MCP server's source (github.com/folathecoder/adzuna-job-search-mcp)
- Base `http://api.adzuna.com/v1/api/jobs/{country}/search/{page}` (docs use `http://`; auth via
  query params `app_id`, `app_key`).
- Confirmed on the official docs page: `app_id`, `app_key`, `results_per_page`, `what` (the primary
  keyword param — not `title_only`), `what_exclude`, `where`, `sort_by` (example value `salary`),
  `salary_min`, `salary_max`, `full_time` (=1), `permanent` (=1), `content-type`. Response `results[]`:
  `id, title, description, redirect_url, created, latitude, longitude, contract_type, contract_time,
  salary_min, salary_max, salary_is_predicted, company.display_name, location.display_name,
  location.area[], category.label, category.tag`.
- The docs page defers the rest to a JS-rendered `/activedocs` page WebFetch can't render (nav shell
  only). `max_days_old` is confirmed present and working in the third-party MCP server's request code
  (2026-09-04); `category`, `part_time`, `contract` likewise appear there.
- **`title_only` and `distance` (both in the draft) were NOT found in the primary docs page nor in the
  third-party server's source** — only third-party blog/marketing mentions exist. Do not assume they
  work without a live test.
- Free/paid tier: **not documented** on any page reached; registration required to see it.
- Rate limits: not documented.
- Gotchas: response fields match the draft exactly; param mismatches are `title_only`/`distance`
  (unconfirmed, see above). `sort_by`'s only documented value is `salary`.
- Sample response (official docs example, trimmed):
  ```json
  {"results": [{"id": "129698749", "title": "Javascript Developer",
    "company": {"display_name": "Corporate Project Solutions"},
    "location": {"display_name": "Marlow, Buckinghamshire"},
    "created": "2013-11-08T18:07:39Z",
    "redirect_url": "http://adzuna.co.uk/jobs/land/ad/129698749...",
    "salary_min": 50000, "salary_max": 55000}]}
  ```

## Reed
- Verified on: 2026-09-04 — via: WebFetch (reed.co.uk/developers/jobseeker, /Jobseeker, /developers)
  + WebSearch (rate limit)
- Base `https://www.reed.co.uk/api/1.0/search` (version confirmed `1.0`). Auth: API key as HTTP Basic
  **username, password blank** — matches draft exactly.
- Params confirmed: `keywords`, `locationName`, `distanceFromLocation` (default 10mi), `resultsToTake`
  (max **100**), `resultsToSkip`, `minimumSalary`, `maximumSalary`, `postedByRecruitmentAgency`,
  `postedByDirectEmployer`, `permanent`, `contract`, `temp`, `partTime`, `fullTime`, `graduate`,
  `employerId`, `employerProfileId`.
- Response fields: docs describe them only in prose (Job Id, Employer Id, Employer Name, Job Title,
  Description, Location Name, Minimum/Maximum Salary) — **exact camelCase JSON keys not independently
  confirmed** (no JSON example on any page fetched). Docs note: hidden salaries show no salary data;
  no matches returns an empty list.
- Free tier: brief's table says "free"; Reed's own pages state no price and **no paid tier is
  documented at all**.
- Rate limits: **not on Reed's own docs**; third-party only (publicapi.dev via WebSearch, 2026-09-04):
  "1,000 requests/day/key" — unverified, indicative only.
- Date format (draft assumes `DD/MM/YYYY`): **not documented** anywhere reachable — no JSON example
  exists on the fetched pages. Do not hardcode without a live test.
- Gotchas: `currency` and `jobUrl` field existence: **not documented** (prose-only field list, no
  example). Get a key and make one live test call before Plan 1 encodes exact field casing.
- Sample response: none available (no JSON example published on any reachable page).

## Jooble
- Verified on: 2026-09-04 — via: WebFetch (jooble.org/api/about — signup form, no technical docs
  without an approved key) + WebSearch (publicapi.dev, help.jooble.org)
- Base/auth: `POST https://jooble.org/api/{key}` — key in path, not a header. `/api/about` is a
  lead-capture form (name/position/email/website/phone); **no reference is published without a key**.
- Request body (third-party only, publicapi.dev): JSON `{"keywords": "...", "location": "..."}`.
  Draft's `page`/`datecreatedfrom` fields **not independently confirmed** anywhere reachable.
- Response (third-party only): `jobs[]` — draft's field names (`id, title, company, location, snippet,
  link, updated, salary, source`) **could not be confirmed against an official source**; no
  vendor-published example found.
- Free tier: **500 requests total, lifetime cap per key — not monthly** (help.jooble.org support
  article, 2026-09-04). Contradicts reading the brief's "free" label as open-ended — it's free but
  capped at a small one-time total, a load-bearing correction for Plan 1's budget math.
- Rate limits: third-party source says "generous," no number given.
- Gotchas: **least-documented service in this table** — everything beyond the URL shape and auth
  mechanism is third-party-sourced because Jooble gates real docs behind manual key approval. Treat
  this adapter as higher-risk; validate every field name against a live response before production use.
- Sample response: none available (no vendor-published example found without a key).

## JSearch (RapidAPI)
- Verified on: 2026-09-04 — via: WebFetch of a same-vendor mirror (openwebninja.com/api/jsearch —
  RapidAPI's own page is a JS SPA shell that WebFetch can't render) + WebSearch cross-check
- Base `https://jsearch.p.rapidapi.com`. Headers `x-rapidapi-key`, `x-rapidapi-host:
  jsearch.p.rapidapi.com` — matches draft, confirmed by both sources.
- `/search` params (mirror): `query` (required), `page`, `num_pages`, `date_posted`
  (all/today/3days/week/month), `country`, `language`, `location`, `employment_types`.
  **`remote_jobs_only` (draft) was not on the mirror's list — it showed `work_from_home` instead**; a
  separate WebSearch hit named neither. Unresolved — confirm the real param name with a live call
  before shipping.
- Response `data[]` (mirror, superset of draft — all draft fields present): `job_id, job_title,
  employer_name, employer_logo, employer_website, job_publisher, job_employment_type,
  job_apply_link, job_description, job_is_remote, job_posted_at_datetime_utc, job_city, job_state,
  job_country, job_latitude, job_longitude, job_min_salary, job_max_salary, job_salary_period,
  apply_options[], job_benefits`.
- Free/paid tiers (**mirror-sourced, not RapidAPI's own page**): Free $0/200 req/mo, 1,000/hr; Pro
  $25/mo for 10,000 (+$0.003/extra); Ultra $75/mo/50,000 (+$0.002); Mega $150/mo/200,000 (+$0.001);
  Pay-As-You-Go $0.005/req, 5 req/s.
- Rate limits: 5 req/s (Pro) to 20 req/s (Mega), mirror-sourced only.
- Gotchas: shape matches the draft except the remote-filter param name (unresolved, see above) and
  pricing/rate limits being third-party mirror data, not RapidAPI's own unreachable page.
- Sample response: not reproduced — mirror gave field names, no full JSON example.
- **Correction, verified live 2026-09-06 (3 requests with our key):** `/search` now returns 404
  `{"message":"Endpoint '/search' does not exist"}`. The endpoint is **`/search-v2`** (same RapidAPI
  host `jsearch.p.rapidapi.com`); the response is `{status, request_id, parameters, data: {jobs: [...],
  cursor}}` — items keep the field names above (plus `job_location`, `job_uid`, `job_publishers`,
  `job_highlights`…). The echoed `parameters` show `page`/`num_pages` are still accepted and
  `include_descriptions: true` is the default; `cursor` paginates. 10 jobs per page in the sample.
  Publisher quality varies: one "Canada" query returned CareerBuilder items with a US city and
  `job_country: CA`, so the location prefilter must stay strict.

## Remotive
- Verified on: 2026-09-04 — via: WebFetch (github.com/remotive-com/remote-jobs-api)
- Base `GET https://remotive.com/api/remote-jobs` — matches draft, no auth.
- Params confirmed: `search`, `category`, `company_name`, `limit` (draft's `search`/`limit` both
  confirmed; `category`/`company_name` are additional, not a mismatch).
- Response `jobs[]` confirmed: `id, url, title, company_name, company_logo, category, job_type,
  publication_date, candidate_required_location, salary, description` — all draft fields present and
  matching exactly. `description` is full HTML.
- Free tier: unlimited but throttled (below); no paid tier for this API (separate from Remotive's
  paid job-posting product).
- Rate limits / gotchas: **"Excessive requests (more than 2x per minute) will be blocked"; recommended
  max 4x/day. Listings delayed 24h. Attribution + backlink required per ToS.** The single most
  concrete rate-limit finding here — Plan 1's scheduler must not poll Remotive more than a few times
  daily.
- Sample response (trimmed):
  ```json
  {"jobs": [{"id": 123456, "url": "https://remotive.com/remote-jobs/...",
    "title": "...", "company_name": "...", "candidate_required_location": "USA",
    "publication_date": "2026-09-01T00:00:00", "salary": "$90,000 - $110,000",
    "description": "<p>...</p>"}]}
  ```

## WeWorkRemotely RSS
- Verified on: 2026-09-04 — via: WebFetch of the feed URL itself (the brief's "Docs" column names the
  feed URL directly — this is method-2 docs-fetch, not a live probe of a third service)
- URL `https://weworkremotely.com/remote-jobs.rss`. No auth.
- Format: **RSS 2.0** (not Atom), with Dublin Core (`dc`) and Media RSS (`media`) namespaces declared.
- `<item>` elements observed: `title`, `region`, `country`, `state`, `skills`, `category`, `type`
  (employment type), `description` (HTML — overview/responsibilities/requirements/compensation all
  embedded, not separated), `pubDate`, `expires_at`, `guid`, `link`.
- Free tier / rate limits: not documented — plain public RSS, no stated cap.
- Gotchas: `region`/`country`/`state` were frequently empty in items observed — don't assume location
  is always structured; `description` is the only place salary/comp appears, same free-text-parsing
  problem as Job Bank's `summary` below.
- Sample: element names only, no content reproduced (see Verified-on note).

## Job Bank Canada RSS
- Verified on: 2026-09-04 — via: **live curl probe, twice.** The brief's exact command
  (`.../jobsearchfeed?searchstring=...&locationstring=...&sort=M`) returned **HTTP 404** — a real,
  server-rendered "HTTP Error 404 - Not Found," not a network failure: the brief's URL is stale. A
  follow-up probe (Job Bank only, no other service) found the real path via the human search page's
  own emitted RSS link.
- **Real, working shape (HTTP 200, `Content-Type: application/atom+xml`), two-step and session-bound:**
  1. `GET .../jobsearch/jobsearch?searchstring={term}&locationstring={loc}&sort={sort}` → HTML with a
     `JSESSIONID` cookie and an embedded feed link: `/jobsearch/feed/jobSearchRSSfeed;jsessionid={ID}
     .jobsearch76?fage=2&fcid=...&fn21=...&term={term}&sort=D&rows=100` (`fcid`/`fn21` are Job Bank's
     own taxonomy ids auto-selected server-side from the term — not constructible standalone).
  2. `GET` that feed URL **with the same session cookie**. Untested without the matching cookie —
     don't assume it works without one.
- **Format is Atom, not RSS 2.0**, despite the "RSS" name. `<entry>` fields: `title` (CDATA),
  `link[rel=alternate href=.../jobsearch/jobposting/{id}]`, `id` (feed-internal URL, not the job
  number), `updated` (ISO 8601), and one `summary` field with **job number/location/employer/salary
  embedded as `<strong>label:</strong> value<br/>` pairs in one HTML blob** — no separate XML elements
  for those; a parser must HTML-parse `summary`.
- Free tier / rate limits: not documented — no official API exists for this feed at all; it's a
  byproduct of the human search UI.
- Gotchas: brief's URL is dead, use the two-step flow above; format is Atom not RSS 2.0; `summary`
  needs HTML-blob parsing; `<entry><id>` is not the numeric job id used in `<link href>` — extract the
  id from the link, not `<id>`.
- Sample (element names only — no scraped titles/employers/salaries reproduced, per brief's rule):
  ```xml
  <feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <title type="html"><![CDATA[...]]></title>
    <link rel="alternate" type="text/html" href=".../jobsearch/jobposting/{id}"/>
    <id>.../jobSearchRSSfeed?id={feed_internal_id}</id>
    <updated>2026-09-03T16:00:00Z</updated>
    <summary type="html"><![CDATA[<strong>Job number:</strong> ...<br/><strong>Location:</strong>
      ...<br/><strong>Employer:</strong> ...<br/><strong>Salary:</strong> ...]]></summary>
  </entry></feed>
  ```

## LinkedIn Job Search API (Fantastic Jobs, RapidAPI) — added 2026-09-06
- Verified on: 2026-09-06 — via: 6 live requests with our key (free Basic plan) + context7's index of
  the RapidAPI listing. Replaces the guest scraper (429 on every call after day 0).
- Host `linkedin-job-search-api.p.rapidapi.com`; headers `x-rapidapi-key`, `x-rapidapi-host`.
  Endpoints `/active-jb` (used) and `/search` (adds `description`, `date_posted_gte/lt`,
  `date_created_gte`, `organization`, `*_advanced`).
- `time_frame` is **required** on `/active-jb`: `1h | 24h | 7d | 6m` (400 without it). `title` and
  `location` take Google-style OR syntax with quoted phrases (`"data analyst" OR "hr analyst"`,
  `Canada OR "United Kingdom"`; full country names, not codes). `title_advanced` has its own boolean
  grammar and **rejected** a quoted OR list (400 "Invalid syntax in advanced search expression") — not
  used. `limit` default 10 (100 on the About page), max 1,000; `offset`; `description_format=text`
  returns `description_text`. `exclude_organization=<name>` works on `/active-jb` (one name verified).
- Response: a JSON **list** of jobs. Fields used: `id`, `linkedin_id`, `title`, `organization`, `url`,
  `date_posted` (ISO, no tz → UTC), `description_text`, `locations_derived[]` ("Leeds, England, United
  Kingdom"), `countries_derived[]`, `ai_work_arrangement` ("Remote OK" / "Hybrid" / …), `seniority`,
  `salary`, `employment_type[]`, `org_linkedin_recruitment_agency_derived`. Also present: 20+ `ai_*`
  enrichments incl. `ai_hiring_manager_name` / `ai_hiring_manager_email_address` (null in the sample),
  `ai_visa_sponsorship`, org LinkedIn data.
- Credits: each call spends 1 request credit and N job credits (N = jobs returned); response headers
  `x-ratelimit-requests-remaining`, `x-ratelimit-jobs-remaining`, `*-reset` (seconds). **Free Basic:
  25 requests + 250 jobs / month**; Pro $45/mo 10,000 jobs / 5,000 requests; Ultra $95; Mega $175.
  A 400 still costs a request credit. Adapter: 1 call/day, `page_size` 8.
- Quality: agencies dominate ("Jobright.ai" re-posts new-grad roles under its own name — 6 of the
  first 8; excluded via `exclude_organizations`; Experis / Quik Hire / Robertson follow). The
  `org_linkedin_recruitment_agency_derived` flag is stored in `extra` for later use.
  **`organization_agency=exclude` verified live on `/active-jb` (2026-09-06):** the same query went
  from 7 agencies + 1 employer to 8 employers. Also documented (context7): `seniority` (comma list of
  LinkedIn levels), `organization_slug` / `exclude_organization_slug`, `organization_industry`,
  `organization_headcount_gte/lt`, `/ats-organizations` for slugs. Comma-separated lists are the
  documented convention for those; `exclude_organization` with several names is joined the same way
  but only one name was verified.

## Active Jobs DB (Fantastic Jobs, RapidAPI) — added 2026-09-06
- Verified on: 2026-09-06 — via: 4 live requests with our key (free Basic). Employer career-site
  postings across 54 ATS platforms (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Workday,
  Oracle Cloud, Eightfold, …), 200k employers, hourly refresh — the feed our five token-less ATS
  board adapters could never get.
- Host `active-jobs-db.p.rapidapi.com`, endpoint **`/active-ats`** (`/active-ats-24h`, `/active-ats-7d`
  → 404 "does not exist"). Same dialect as the LinkedIn Job Search API: `time_frame` required,
  `title` / `location` Google-style OR, `description_format=text`, `limit`/`offset`,
  `exclude_organization`, `organization_agency`. The older names `title_filter`, `location_filter`,
  `description_type` are **rejected** (400 "Unknown query parameter"). Free Basic: **25 requests +
  250 jobs / month** (headers `x-ratelimit-requests-*`, `x-ratelimit-jobs-*`); a 400 costs a request.
- Response: JSON list; same fields as the LinkedIn feed minus `linkedin_id`, `seniority`,
  `org_linkedin_recruitment_agency_derived`, `direct_apply`; `source` = ATS name, `source_type` =
  "ats", `source_domain` = employer or ATS host, `url` = the employer's own posting page. One
  Eightfold item lacked `countries_derived` (adapter falls back to the location string).
- Live sample (7d window, agencies excluded): 8 postings, 2.3–7.8k-char descriptions, Toronto /
  Windsor QC / Saint John / Slough / Edinburgh / London; a 24h window returned 2 for the 14 keywords.

## Indeed by Mantiks (RapidAPI `indeed12`) — probed 2026-09-06, not adopted
- `/jobs/search?query=&location=&page_id=&locality=ca&fromage=&sort=` answered **429 then 500
  "[FallbackScraper] all proxies failed"** on two attempts (different queries, a minute apart); the
  failures were not charged. Free plan **25 requests / month**; search hits carry no description, so
  each job needs a second request. Unreliable and too small to matter — no adapter. the user may unsubscribe.

## LinkedIn guest search
- Verified on: 2026-09-04 — via: **live curl probe**, exact brief command (`curl -sL -A "Mozilla/5.0"
  ".../jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=data%20analyst&location=Montreal%2C%20
  Quebec%2C%20Canada&f_TPR=r604800&start=0"`)
- Result: **HTTP 200**, `grep -c base-card` = 30 (each card contributes 3 class-name matches —
  `base-card`, `base-card__full-link`, `base-search-card` — so 30 ⇒ **10 cards per page**, not 30
  jobs). No official docs — unofficial, reverse-engineered endpoint behind the guest job-search UI; can
  change or block without notice.
- Elements observed per card: `h3.base-search-card__title` (job title), `h4.base-search-card__subtitle`
  (company), `.job-search-card__location`, `.job-search-card__metadata`, `.job-search-card__listdate`
  / `--new` with `datetime="YYYY-MM-DD"`, `data-entity-urn="urn:li:jobPosting:{numeric_id}"` (job id),
  and `<a class="base-card__full-link">` → `href=https://{region}.linkedin.com/jobs/view/{slug}-
  {numeric_id}?position=...&trackingId=...` (region subdomain varies, e.g. `ca.`; querystring is
  tracking noise). No `description` here — only the detail page has it, out of scope (never fetch
  LinkedIn detail pages, per the project's own constraint).
- Free tier / rate limits: none documented. `start` paginates in increments of the observed page size
  (10); `f_TPR=r604800` is a relative time window in seconds (604800s = 7 days) — format confirmed
  working live, semantics inferred from LinkedIn URL convention, not documentation.
- Gotchas: unofficial — no SLA/rate-limit/versioning guarantee, the most fragile source here. Region
  subdomain (`ca.linkedin.com` seen, not `www.`) means link-domain shouldn't be hardcoded when
  dedup'ing by URL.
- Sample: element/class names only; no scraped titles/companies reproduced.

## Greenhouse boards API
- Verified on: 2026-09-04 — via: WebFetch (developers.greenhouse.io/job-board.html → redirects to
  docs.greenhouse.io/job-board.html) + WebSearch (rate limit)
- `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs`. **No auth for any GET** — docs
  state explicitly "Job Board data is publicly available." Matches draft exactly.
- `content=true` adds `content` (full description) plus `departments`/`offices`; without it, a lighter
  shape. Confirmed fields: `id, internal_job_id, title, updated_at, requisition_id, location.name,
  absolute_url, language, metadata` (+ `content, departments, offices` with content=true). All draft
  fields present, matching exactly.
- Content handling: confirmed HTML-escaped ("converted into corresponding HTML entities," e.g.
  `&lt;p&gt;`) — matches draft's note exactly.
- Free tier: unlimited/public. Rate limits: **not officially published for the Job Board API**
  (a separate, authenticated Harvest API documents 50 req/10s — doesn't apply here); WebSearch found
  only unofficial advice to poll on a schedule rather than per page-view.
- Gotchas: none vs. the draft — cleanest match of the ten named services. Pagination undocumented
  (no `page`/`offset` found) — appears to return the full list in one response.
- Sample response (trimmed):
  ```json
  {"jobs": [{"id": 4012345, "title": "Data Analyst",
    "updated_at": "2026-09-01T12:00:00-04:00", "location": {"name": "Montreal, QC"},
    "absolute_url": "https://boards.greenhouse.io/example/jobs/4012345",
    "content": "&lt;p&gt;We are looking for...&lt;/p&gt;"}]}
  ```

## Lever postings API
- Verified on: 2026-09-04 — via: WebFetch (github.com/lever/postings-api, both rendered page and raw
  README via raw.githubusercontent.com)
- `GET https://api.lever.co/v0/postings/{site}?mode=json` (EU tenants: `api.eu.lever.co/...` — check
  which region a target company uses). Same slot as draft's `{company}`.
- Confirmed fields: `id, text, hostedUrl, applyUrl, categories` (`location, commitment, team,
  department, allLocations`), `workplaceType` (unspecified|on-site|remote|hybrid), `descriptionPlain,
  description` (HTML), `lists[]` (`{text: NAME, content: "HTML"}`), `salaryRange` (currency/interval/
  min/max, optional), `country` (ISO alpha-2). Also confirmed but not in draft: `opening/openingPlain`,
  `descriptionBody/descriptionBodyPlain`, `additional/additionalPlain`,
  `salaryDescription/salaryDescriptionPlain`.
- **`createdAt` (draft assumes ms-since-epoch) does NOT appear anywhere in Lever's own README** —
  checked twice (rendered + raw markdown), neither mentions it. Confirmed mismatch: Plan 1 cannot rely
  on a creation timestamp from this API; recency filtering needs another signal (e.g. re-poll and diff
  by `id`).
- Filters confirmed: `location`, `team`, `commitment`, `department` (multi-value OR, case-sensitive),
  plus `level` and a `group` grouping param. Pagination: `skip`, `limit` — no documented max.
- Free tier / rate limits: free, public, no auth; no rate limit documented in the README.
- Gotchas: **`createdAt` mismatch above is the headline finding for Lever** — flag before Plan 1 codes
  against that field.
- Sample response (trimmed):
  ```json
  [{"id": "abc-123", "text": "Data Analyst",
    "hostedUrl": "https://jobs.lever.co/example/abc-123",
    "applyUrl": "https://jobs.lever.co/example/abc-123/apply",
    "categories": {"location": "Montreal, QC", "team": "Data"},
    "workplaceType": "hybrid", "descriptionPlain": "...",
    "lists": [{"text": "Requirements", "content": "<ul>...</ul>"}]}]
  ```

## Ashby job board API
- Verified on: 2026-09-04 — via: WebFetch of the brief's given URL (wrong — see below) + WebSearch to
  locate the correct docs page + WebFetch of that page
- **The brief's docs URL (`/reference/jobpostinglist`) points to Ashby's INTERNAL, authenticated API
  (`POST jobPosting.list`, needs `jobsRead` permission) — not the public API the draft's shape
  describes.** Confirmed mismatch in the brief itself. Correct docs:
  `https://developers.ashbyhq.com/docs/public-job-posting-api`.
- Correct base: `GET https://api.ashbyhq.com/posting-api/job-board/{jobBoardName}` — no auth
  (confirmed). Matches the draft's assumed endpoint shape once pointed at the right page.
- Query param: `includeCompensation` (true/false) — adds `compensation` when exposed.
- Confirmed fields: `title, location, secondaryLocations, department, team, isRemote (bool),
  workplaceType (OnSite|Remote|Hybrid), descriptionHtml, descriptionPlain, publishedAt (ISO),
  employmentType (FullTime|PartTime|Intern|Contract|Temporary), address (postalAddress:
  addressLocality/addressRegion/addressCountry), jobUrl, applyUrl, isListed (bool), compensation`
  (only with includeCompensation=true); top-level also carries `apiVersion`. **`id` was not in the
  fetched field summary** — practically keyed by `jobUrl`'s slug, but a distinct `id` field's presence
  is unconfirmed; verify with a live call.
- Free tier / rate limits: free, public. Rate limit **not officially documented** — WebSearch found
  only community reports of an unofficial ~100 req/min per-IP limit (apis.io, jobspipe.dev,
  2026-09-04), explicitly not from Ashby's own docs.
- Gotchas: brief's docs URL wrong (internal vs public) — use `/docs/public-job-posting-api`; `id`
  field presence unconfirmed; `isRemote`/`descriptionHtml`/`descriptionPlain` all match.
- Sample response (trimmed):
  ```json
  {"jobs": [{"title": "Data Analyst", "location": "Montreal, QC", "isRemote": false,
    "workplaceType": "Hybrid", "employmentType": "FullTime",
    "publishedAt": "2026-09-01T00:00:00.000Z",
    "jobUrl": "https://jobs.ashbyhq.com/example/abc-123",
    "applyUrl": "https://jobs.ashbyhq.com/example/abc-123/application",
    "descriptionPlain": "..."}], "apiVersion": "1"}
  ```

## Workable widget API
- Verified on: 2026-09-04 — via: WebFetch (workable.readme.io/reference/jobs — the OFFICIAL
  authenticated reference) + WebFetch (help.workable.com career-page article — the DIFFERENT public
  widget) + WebSearch corroboration
- **Two separate, non-interchangeable Workable APIs exist**; the draft's endpoint is the second one,
  not the one at the brief's `workable.readme.io/reference/jobs` URL:
  1. **Official authenticated SPI v3** (`workable.readme.io`) — `https://{subdomain}.workable.com/
     spi/v3`, needs an OAuth/API token scoped `r_jobs`. This is what `/reference/jobs` documents.
  2. **Public unauthenticated "careers page" widget** (only in a Help Center article, not the main
     reference) — `GET https://www.workable.com/api/accounts/{subdomain}?details=true`. This is what
     the draft assumes, confirmed real and public via help.workable.com.
- Widget fields confirmed: `id, title, full_title, shortcode, code, state, department,
  department_hierarchy, url, application_url, shortlink, location (location_str, country,
  country_code, city, zip_code, telecommuting, workplace_type), locations[], salary (salary_from,
  salary_to, salary_currency), created_at`.
- **Conflicting evidence on `description`/`requirements`/`benefits`/`published_on` with
  `details=true`:** help.workable.com's sample response did *not* show them and said full description
  needs the separate authenticated `/jobs/:shortcode` endpoint instead; a separate WebSearch result
  claimed `details=true` "includes description and full_description." Sources disagree —
  **unresolved; live-test one real subdomain before assuming `description` is present**, with a
  fallback to `/jobs/:shortcode` if not.
- Free tier / rate limits: free, public (widget); not documented anywhere fetched.
- Gotchas: two different APIs share the "Workable" name — don't mix field shapes; the
  description/requirements/benefits/published_on question above is the top open item; job `state`
  (posting status) vs. `location.state`-style region fields share a name but differ — parse carefully.
- Sample response (widget, confirmed fields only, trimmed):
  ```json
  {"name": "Example Inc", "jobs": [{"id": 987654, "title": "Data Analyst", "shortcode": "ABCD123",
    "url": "https://apply.workable.com/example/j/ABCD123/",
    "application_url": "https://apply.workable.com/example/j/ABCD123/apply/",
    "location": {"city": "Montreal", "country": "Canada", "telecommuting": false},
    "created_at": "2026-09-01T00:00:00.000Z"}]}
  ```

## SmartRecruiters posting API
- Verified on: 2026-09-04 — via: WebFetch (developers.smartrecruiters.com/reference/v1listpostings-1,
  /reference/v1getposting-1) + WebSearch to locate them (the brief's `/reference/postingsall` returned
  only a generic overview on every attempt, existence unconfirmed)
- `GET https://api.smartrecruiters.com/v1/companies/{companyIdentifier}/postings` — **no auth**
  (public), matches the draft exactly. (A separate `/feed/publications` endpoint needs partner
  `X-SmartToken` auth and aggregates across *all* customers — not what Plan 1 wants, noted to avoid
  confusion.)
- Query params confirmed: `q`, `limit` (max 100), `offset`, `destination`
  (PUBLIC/INTERNAL/INTERNAL_OR_PUBLIC), `locationType` (REMOTE/HYBRID/ONSITE/ANY), `country`,
  `region`, `city`, `department`, `jobAdId`, `language`, `releasedAfter`, `customField`.
- List response confirmed: `totalFound, limit, offset, content[]` with `id, uuid, name, jobAdId,
  refNumber, releasedDate, location (city, region, country, remote, hybrid), ref, company, department,
  function, typeOfEmployment, experienceLevel, customField, language, visibility` — all draft fields
  present and matching.
- Detail confirmed: `GET .../postings/{postingId}` (the `ref` URL resolves here, matching draft's
  "detail `GET {ref}`" conceptually). Adds `applyUrl, postingUrl, referralUrl, jobId, active,
  compensation (min, max, currency, period)`, and `jobAd`.
- **Mismatch: `jobAd` is NOT a generic `{title, text}` sections array as the draft assumes** — it's an
  object with **fixed, named keys**: `companyDescription, jobDescription, qualifications,
  additionalInformation, videos` (videos holds `urls` instead of text). Plan 1 should read
  `jobAd.jobDescription` etc. directly, not iterate a `sections[]` array.
- Free tier / rate limits: public/free; **300 requests/minute per client** (WebSearch-corroborated
  developers.smartrecruiters.com pages, 2026-09-04) — the one rate limit here with a specific number
  from a plausible primary-adjacent source.
- Gotchas: brief's docs URL unconfirmed to exist — use `v1listpostings-1`/`v1getposting-1`;
  `jobAd.sections.*` mismatch (fixed keys, not array); don't confuse this with `/feed/publications`.
- Sample response (list, trimmed):
  ```json
  {"totalFound": 1, "limit": 100, "offset": 0, "content": [{"id": "abc123",
    "name": "Data Analyst", "releasedDate": "2026-09-01T00:00:00.000Z",
    "location": {"city": "Montreal", "region": "QC", "country": "ca", "remote": false},
    "ref": "https://api.smartrecruiters.com/v1/companies/example/postings/abc123"}]}
  ```

---

## Summary

| Service | Free/mo | Entry paid | Needed for | Plan |
|---|---|---|---|---|
| Apollo.io | not documented (conflicting third-party figures) | ~$49-59/mo (unconfirmed exact) | Person search + email/phone reveal | Plan 3 |
| Hunter.io | 50 credits/mo | $49/mo ($34 annual) — 2,000 credits | Domain search, email finder, verifier | Plan 3 |
| Serper.dev | 2,500 queries (one-time) | $50 / 50,000 queries (third-party) | Web search for domain/person lookup | Plan 3 |
| Adzuna | not documented | not documented | Job search | Plan 1 |
| Reed | not documented | not documented (may be fully free) | Job search (UK) | Plan 1 |
| Jooble | 500 requests (lifetime cap, not monthly) | not documented | Job search | Plan 1 |
| JSearch (RapidAPI) | 200 requests/mo (third-party) | $25/mo — 10,000 (third-party) | Aggregated job search | Plan 1 |
| Remotive | unlimited, throttled (2 req/min) | none (free API) | Remote job listings | Plan 1 |
| WeWorkRemotely RSS | unlimited (undocumented cap) | none | Remote job listings | Plan 1 |
| Job Bank Canada RSS | unlimited (undocumented cap) | none | Canadian government job listings | Plan 1 |
| LinkedIn guest search | unofficial, no stated limit | none (unofficial) | Job search (best-effort, fragile) | Plan 1 |
| Greenhouse boards API | unlimited (undocumented cap) | none | Company career-page listings | Plan 1 |
| Lever postings API | unlimited (undocumented cap) | none | Company career-page listings | Plan 1 |
| Ashby job board API | unlimited (~100 req/min unofficial) | none | Company career-page listings | Plan 1 |
| Workable widget API | unlimited (undocumented cap) | none | Company career-page listings | Plan 1 |
| SmartRecruiters posting API | 300 req/min | none | Company career-page listings | Plan 1 |
