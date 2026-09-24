# Lake County Brief — Notes and Data Recommendations

> **Status: runnable in CrewAI, not yet run.** Added after the capstone submission (2026-09-24). CrewAI only. Run it with `--brief lake-county` (see [Running this brief](#running-this-brief)). All 15 required output sections are produced, including the Census market profile and the equity and compliance check.

Companion to [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) in this folder. It records what was checked while the brief was written, which sources to trust, and which data APIs would give the pipeline cleaner inputs than web search alone.

> **Scope: CrewAI only.** This brief will be run in the CrewAI implementation only. n8n will not be run against it, and there will be no n8n-vs-CrewAI comparison for Lake County. The n8n changes that would be needed are listed under [Not in scope: n8n](#not-in-scope-n8n) in case that changes.

## How this brief differs from Ocala

| | Ocala ([`../../PROJECT_BRIEF.md`](../../PROJECT_BRIEF.md)) | Lake County |
|---|---|---|
| Area | 1 region (Ocala / Marion County) | 2 cities (Leesburg, Mount Dora), each at city-limit and ZIP level |
| Comparison rows | 4 (Wire3 + 3) | 7 (Wire3, Xfinity, CenturyLink, Quantum Fiber, Spectrum, T-Mobile Home Internet, Verizon Home Internet) |
| Segments | 1 (price/value switchers) | 2 (cost-constrained households, value switchers) |
| Research questions | 8 | 11 |
| Required output sections | 13 | 15 (adds market profile by city, equity & compliance check) |

## The geography check

The first draft said "some parts of Leesburg have higher poverty rates and a large share of renters" and "parts of the region skew older, with high homeownership." These were checked against the ACS 2020–2024 5-year estimates on 2026-09-24, first through the Census Reporter API and then through the official Census Data API. Every figure matched.

The result: **"Leesburg" means two different markets depending on the boundary.**

- **Leesburg city limits** (12.8k households) had a median household income of $52,880, 16.4% poverty, 37% renters, and 31% of homes in buildings with 2+ units. That confirms the cost-constrained profile.
- **Leesburg ZIP areas outside city limits** are older (median age 56–60) and mostly owner-occupied. **ZIP 34788 is 51% mobile homes.** This is the fixed-income value-switcher profile, reached through manufactured-home communities.
- **Mount Dora city limits** are 38% renters, so "high homeownership" does not hold there. It is higher-income ($74.5k) and older (48.8).

The brief now requires both boundaries to be reported side by side (Section 2). Places and ZCTAs used:

| Area | Census geography ID |
|---|---|
| Leesburg city | place `1239875` (Census Reporter: `16000US1239875`) |
| Mount Dora city | place `1247050` (`16000US1247050`) |
| Leesburg ZIPs | ZCTA `34748`, `34788` |
| Mount Dora ZIP | ZCTA `32757` |
| Lake County | county `12069` |

ACS tables used: `B19013` (median household income), `B17001` (poverty), `B25003` (tenure), `B25024` (units in structure), `B01002` (median age), `B28002` (internet subscriptions), `C16001` (language spoken at home).

Place code `1239200` is **not** Leesburg. Look codes up rather than guessing them.

## Source reliability (from the Ocala runs)

Domain counts across every CrewAI run in `crewai/runs/` (`02_evidence_set.json`, `02_tool_calls.jsonl`, `06_link_check.json`):

- **Produced evidence:** wire3.com, t-mobile.com, spectrum.com (often "unverified" by the link checker), BroadbandNow, broadbandmap.com (a third-party site, not the FCC's), highspeedinternet.com, Reddit, corporate.charter.com.
- **Listed as preferred but never produced evidence:** the FCC National Broadband Map, Census / data.census.gov, BBB, Trustpilot, J.D. Power, ACSI, Parks Associates, Pew, Wayback Machine, Fierce, Light Reading, and the local newspaper.
- **Blocked most often:** att.com (48 times), allconnect.com, ispreports.org, finance.yahoo.com, whistleout.com.

Changes made in the brief as a result:
- Removed as unreadable through search: the FCC map interface, address serviceability checkers, data.census.gov, Google reviews, the Wayback Machine, and Parks Associates.
- Replaced with the data sources below.

BBB, Trustpilot, J.D. Power, Pew and trade press remain as preferred sources because they are public, but expect them to be thin unless the research tool fetches them directly.

**Watch att.com.** If Quantum Fiber is now under AT&T, its pricing pages may redirect to att.com, which the pipeline has never been able to read.

## Recommended data APIs

Only the Census Data API is wired in so far. Listed by how much they would improve the Lake County run.

| API | What it gives | Research questions | Cost / access | Notes |
|---|---|---|---|---|
| **Census Data API** (api.census.gov) | ACS tables by place and ZCTA; the numbers above, straight from the source | RQ5 | Free; key required (sign up at api.census.gov/data/key_signup.html) | **Implemented** as the `census_profile` tool (see [Census data](#census-data-added-2026-09-24)). Key in `mcp-server/.env`. |
| **Census Reporter API** (api.censusreporter.org) | Same ACS data with simpler JSON | RQ5 (fallback) | Free, no key | Unofficial. Rejects Python's default user agent (403), so send a User-Agent header. Geo search endpoint returned `block`. |
| **FCC Broadband Data Collection public data API** (broadbandmap.fcc.gov) | Fixed-broadband availability by provider, technology and advertised speed at location level | RQ1 | Free; requires an FCC account plus an API token generated in it | State files are large. Pull Florida once, filter to Lake County, and cache the extract instead of querying per run. |
| **Ookla Open Data** (speed-test tiles, public S3 bucket) | Measured fixed and mobile download/upload speeds and latency by quarterly tile | RQ1, RQ8 (tests "DSL is slow" and "fixed wireless varies") | Free, no key | Measured speeds give evidence beyond what providers advertise. |
| **USAC Open Data** (opendata.usac.org) | Affordable Connectivity Program enrollment history and Lifeline data | RQ4 | Free, no key (Socrata) | Check which datasets reach ZIP level before relying on them. ZIP-level ACP enrollment would show how many households lost the subsidy. |
| **Wayback Machine CDX / availability API** (archive.org) | Dated snapshots of provider pricing pages | RQ2 (promo vs. post-promo history) | Free, no key | Excluded as a search source, but reliable through its API. Would let Wayback come back into the brief. |
| **Firecrawl** (already referenced in this repo) or Tavily Extract | Rendered page text for JavaScript-heavy provider sites | RQ2, RQ3 | Paid with free tier | Targets the spectrum.com "unverified" and att.com "blocked" failures. |
| **Reddit API** | Posts and comments from local subreddits | RQ6, RQ8 | Free for non-commercial use with an OAuth app | Optional. Reddit already comes through search well (250 hits across Ocala runs). |

**Not recommended:** the Google Places API for reviews. It is paid, returns at most 5 reviews per place, and its terms restrict storing them.

## Budget

Based on the four full Ocala CrewAI runs:
- **Search calls:** 15–18.
- **LLM cost:** $0.12–$0.18.
- **Wall-clock:** 6.0–9.6 minutes. The Analyst step alone took 150–413 s.

The Lake County brief has 7 comparison rows (vs. 4), 11 questions (vs. 8) and 2 cities, which is roughly 2× the load. Estimates:

| | Estimate | Previous cap | Cap in Section 7 |
|---|---|---|---|
| Search calls | ~32–40 | 40 | 60 |
| Cost | ~$0.35–$0.50 | $2.50 | $2.50 |
| Wall-clock | ~12–19 min | 12 min | 20 min |
| LLM calls (Analyst) | 4–6 | 6 | 8 |

These are extrapolations, not measurements; no full Lake County run has been made yet. Live API calls during a run count against the 60-call cap. One-time cached pulls (the FCC extract, Census tables) do not (Section 7).

## Running this brief

```bash
cd mcp-server && uv run python main.py                                          # terminal 1: research server
cd crewai && uv run python -m wire3_gtm run --brief lake-county --docs google   # terminal 2
uv run python -m wire3_gtm executive-brief LAKE-YYYYMMDD-HHMMSS                 # optional CEO version
```

`--brief lake-county` loads [`brief.json`](brief.json) from this folder. Without it, the pipeline runs the Ocala brief (the repo-root `brief.json`) exactly as before: same prompts, same schema sent to OpenAI, same `run-...` run IDs, and the KPI script and golden test still pin Ocala.

### What changed in the pipeline to make this possible

- **Brief selection.** `--brief NAME` loads `briefs/NAME/brief.json`. A resumed run keeps the brief it started with, and commands that take a run ID (`docs`, `executive-brief`, `salvage`, ...) switch to that run's brief automatically (`crewai/wire3_gtm/brief_context.py`).
- **Competitor names from the brief.** This brief's `competitor_names` sets the 7 table rows. The data models check names and row counts against the active brief, and the JSON schema sent to OpenAI lists the same 7 names, so the model cannot return an Ocala name. `competitor_aliases` tells the Analyst how to map spellings like "Comcast" to "Xfinity".
- **Prompts filled from the brief.** `config/agents.yaml` and `config/tasks.yaml` use `[[...]]` markers for names, row counts and the target segment. The brief's `framing_rules` (no stigmatizing descriptors, no excluding neighborhoods, Wire3 facts only from its website, hypothetical framing) are passed to the Strategy agent, which never sees the brief itself.
- **Readable IDs.** Runs are named `LAKE-YYYYMMDD-HHMMSS`, every log event carries `"brief_key": "wire3-lake-county"`, and the Google Doc title uses the run ID (`Wire3 GTM Plan - LAKE-... - <date> UTC`).

Checked on 2026-09-24: `tests/test_brief_context.py` (15 tests), and a Head Planner-only call on this brief (about 1 cent), which returned a valid plan with all 7 competitors, all 11 research questions, 28 of 60 planned calls and the brief's budget.

### Census data (added 2026-09-24)

The brief's `census` field lists six geographies (Leesburg city, ZIPs 34748 and 34788, Mount Dora city, ZIP 32757, Lake County). After the planned searches, the pipeline calls the research server's new `census_profile` tool once per geography (`run_census` in `crewai/wire3_gtm/research_tools.py`). That is 6 calls, counted against the 60-call budget, with no AI involved. Each geography returns four evidence records (income and poverty, housing, age and language, internet access), tagged RQ5 and classed as primary sources.

- **Market profile by city** is now a section of the GTM document, built directly from that evidence, so every figure is exactly what the Census API returned and carries its citation. The Analyst also receives the records like any other evidence.
- **The executive brief** gets one line per area (income, poverty and housing) under "What the market looks like".
- The server needs `CENSUS_API_KEY` in `mcp-server/.env`; the preflight stops the run early if it is missing.

Checked: `mcp-server/tests/test_census.py` (8 tests), `crewai/tests/test_census_research.py` (10 tests), and a live fetch of all six geographies through the running server, which returned the figures in [The geography check](#the-geography-check).

### Equity and compliance check (added 2026-09-24)

A section of the GTM document for briefs with `framing_rules` (not Ocala), placed before Risks:

- **Rules this plan was held to:** the brief's `framing_rules`, which the Strategy agent also receives in its prompt.
- **Automated screen** (`crewai/wire3_gtm/equity_checks.py`): every recommendation the Strategy agent wrote is screened for places described by crime or safety, and for recommendations to exclude, deprioritize or charge more by income, housing type, age, language or ethnicity. Negations ("do not exclude renters") pass, and pains and risk descriptions are skipped because they describe the market, not Wire3's choices. Anything flagged is listed in the section and in the run summary, and marks the run degraded. It does not block the run: it is a keyword screen, not a legal review.
- **Where the plan targets specific customer profiles:** which channels reach each profile, with the rule that targeting may change outreach but not availability, price or service quality.
- **Consumer-protection and digital-discrimination considerations:** the brief's `compliance_considerations` (FCC broadband labels, FTC Act and Florida FDUTPA on promo pricing, FCC digital-discrimination rules, affordability-program claims, multiple-tenant agreements), labeled as not researched in the run.

Checked: `crewai/tests/test_equity.py` (15 tests). The latest Ocala run's document, rebuilt with this code, is identical to the one it produced.

### Still missing

- **RQ9–RQ11** (analogues, channels, local partners) still get only what the company-centered searches return.
- **`schemas/*.json` and `schemas/docs_field_map.json` still describe Ocala.** They are the contract shared with n8n and are left unchanged; the CrewAI models follow the active brief instead.

Each CrewAI run creates a new Google Doc (a resumed run reuses its doc) plus local `05_document.md` and `05_document.pdf` copies. The Ocala CrewAI runs left 7 docs in Drive, including some from failed runs that were created before the write step. Expect the same pattern here.

## Executive brief

`uv run python -m wire3_gtm executive-brief RUN_ID` (in `crewai/`) turns a finished run into a short CEO version of the GTM plan, saved next to the full plan as `05_executive_brief.md`. The code is in `crewai/wire3_gtm/executive_brief.py`.

It covers the recommendation, why we can win (including answers to the no-mobile-bundle and low-brand-recognition gaps), the market, targets, the competitive landscape, plan and timing, spend by budget tier, metrics, top risks, and open questions.

It uses no AI and adds no new facts. It reuses the full plan's validation, drops internal IDs from the text, marks unsourced recommendations "(judgment)", lists its sources at the end, and puts the "illustrative, not Wire3's actual plan" warning at the top and bottom. It reads competitor names from the run's own data, so it will not need changes when the pipeline supports Lake County's 7 competitors.

Not done yet: writing it as a Google Doc (the same `to_docs_requests` path the full plan uses would work; it would add one Google Doc per run), and running it automatically at the end of a run.

A sales version was tried and dropped. **Later (Option B):** an AI step that rewrites the plan in plain language with objection scripts and talk tracks. It would need its own budget line and a grounding check tying every line back to a message pillar or evidence, so it can't invent prices, speeds or guarantees.

## Not in scope: n8n

n8n will not be run for this brief. If that changes, it needs the same work as CrewAI plus:
- A `--brief` option in `n8n/scripts/run_pipeline.js` (it posts the root `brief.json` today).
- The Ocala competitor names and 4-row tables replaced in the workflow's validation and Docs Writer nodes (`n8n/workflows/wire3_gtm_pipeline.json`).
- Doc titles changed in `n8n/docs_writer/build_docs_content.js` and in its copy inside `n8n/workflows/wire3_gtm_pipeline.json` (the workflow file is what runs). n8n titles docs with the hash `brief_id` today.
- `brief_key` added to `n8n/scripts/run_record.js`.
- n8n cost logging is a lower bound (it cannot see hidden reasoning tokens or search fees), so its runs would need a separate cost check against the $2.50 cap.
