# Lake County Brief — Notes and Data Recommendations

> **Status: planned, not yet runnable.** Added after the capstone submission (2026-09-24). CrewAI only. The pipeline currently runs only the Ocala brief ([`brief.json`](../../brief.json)); see [Before this brief can run](#before-this-brief-can-run) for what has to change first.

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

None of these are wired into the CrewAI implementation yet. Listed by how much they would improve the Lake County run.

| API | What it gives | Research questions | Cost / access | Notes |
|---|---|---|---|---|
| **Census Data API** (api.census.gov) | ACS tables by place and ZCTA; the numbers above, straight from the source | RQ5 | Free; key required (sign up at api.census.gov/data/key_signup.html) | Calls without a key are redirected to a "missing key" page. Store it as `CENSUS_API_KEY` in `.env`. |
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

These are extrapolations, not measurements. The pipeline cannot run this brief yet (see below). Live API calls during a run count against the 60-call cap. One-time cached pulls (the FCC extract, Census tables) do not (Section 7).

## Before this brief can run

The pipeline is still Ocala-specific:
- The competitor names `Wire3 / Spectrum / AT&T / T-Mobile Home Internet` are fixed in `crewai/wire3_gtm/analyst_models.py`, `golden.py`, `kpi.py`, `config/tasks.yaml`, `config/agents.yaml` and `schemas/strategy_artifact.schema.json`.
- `schemas/analyst_artifact.schema.json` requires exactly 4 comparison rows.
- `schemas/docs_field_map.json` has an Ocala column label ("Footprint in Ocala").
- `schemas/gtm_document_template.md` has 13 sections; this brief needs 15.
- There is no Lake County `brief.json` yet.

### Run identification

Lake County runs are separable from Ocala runs today only by `brief_id`, a hash of `brief.json` (`crewai/wire3_gtm/run_record.py`). That is hard to use:
- The hash is unreadable (`brief-d516e1daddfb` is Ocala), and every edit to `brief.json` produces a new one.
- Readable run IDs are invented by the Head Planner (`W3-OCALA-001`, `wire3-ocala-r1`, `r3Wk9q`, ...). `config/agents.yaml` still says "Ocala", so a Lake County run could be labeled Ocala.
- Only `run_record` events carry `brief_id`; every other event has to be joined through `client_run_id`.
- CrewAI reads the single root `brief.json` (n8n reads the same file). Swapping in Lake County would drop Ocala runs from the KPI script (`kpi.py`) and fail the golden test, which pins the Ocala brief by sha256 (`golden.py`).

Changes needed:
- Add a readable `brief_key` to each brief (e.g. `"wire3-lake-county"`) and write it on every log event alongside `brief_id`.
- Generate run IDs in code from `brief_key` + date + sequence (e.g. `LAKE-20261001-01`) instead of letting the Head Planner name them.
- Store briefs separately (e.g. `briefs/ocala/brief.json`, `briefs/lake-county/brief.json`) and add a `--brief` option to the CrewAI entry point, so KPIs and the golden test stay tied to their own brief.
- Put `brief_key` and the generated run ID in the CrewAI Google Doc title (e.g. `Wire3 GTM Plan - wire3-lake-county - LAKE-20261001-01 - <date> UTC`). Today CrewAI titles docs with the Head Planner's run ID (`crewai/wire3_gtm/docs_content.py`), so the title does not name the region.

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
