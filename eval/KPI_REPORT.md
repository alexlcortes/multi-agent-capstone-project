# Capstone KPIs: n8n vs CrewAI

Measured 2026-09-23 for brief `brief-d516e1daddfb` (the current `brief.json`). Every number comes from saved
run data and is recomputed by one command:

```
cd crewai && uv run python -m wire3_gtm kpi --recheck-links
```

That writes `eval/kpi/results.json` (all figures below) and `eval/kpi/evidence_tiers.csv` (every evidence
record with its source tier and the rule that assigned it). The calculation is `crewai/wire3_gtm/kpi.py`;
its counting rules are pinned by `crewai/tests/test_kpi.py`.

## Summary

| KPI | Target | CrewAI | n8n |
|---|---|---|---|
| 1. Research coverage | ≥ 90% | **100%** in all 4 full runs: met | **50%** (1 run): not met |
| 2a. Source quality: top-tier share | ≥ 80% | **66–73%**: not met | **82%** (1 run, 11 sources): met, on a small base |
| 2b. Broken links | 0% | **0%**: met | **0%** (re-checked today only): met |
| 3. Latency | < 15 min | **7.1–11.6 min**: met | **7.7 min** (1 run): met |
| 4. Strategy quality | ≥ 4 / 5 | **not measured yet** (model first pass 3.3) | **not measured yet** (model first pass 2.3) |
| 5. Reproducibility | ≥ 80% | **88%** end to end; 92% with fixed evidence: met | **cannot be measured** (1 completed run) |
| 6. Cost within budget | ≤ $2.50/run | **$0.11–0.20**: met | **≥ $0.35** (lower bound): met |

The comparison is uneven. CrewAI has 4 completed full runs of the current brief, n8n has 1, and that one
finished degraded. n8n's other runs of this brief failed (2) or were deliberate budget-cap tests. Its
successful runs were of earlier brief versions and are not counted. **To finish the comparison, n8n needs
at least 3 completed runs of this brief (for KPI 5), and both implementations need a blind human rubric
review (for KPI 4).**

Which runs count everywhere below: completed runs of the current brief with their artifacts saved.
Budget-cap tests (runs with a deliberately lowered budget) are excluded; failed runs count only towards
reliability. The 6 A/B runs (`eval/ab/analyst-sources/`) reuse the golden run's research, so they are used
only for "fixed evidence" figures, never mixed with full runs.

---

## Before KPI 2: classifying every evidence record

The capstone guide defines three tiers:

- **primary** (primary / high quality): official pages of the company a claim is about, company press
  releases (including syndicated copies), government and regulator pages, filings;
- **top_secondary** (top-tier secondary): peer-reviewed or academic work, respected analyst and research
  firms, industry bodies, and news or trade press with named authors;
- **weak** (weak / supporting): affiliate comparison sites, content farms, AI-generated summaries,
  anonymous or user-generated content (forums, social posts, video uploads, reviews), directories, job
  boards, vendor and agency blogs, and index or tag pages that carry no claim.

**How:** the rules are in `eval/kpi/source_tiers.yaml`, one line per domain (132 domains), with a reason on
each. Domains that host several kinds of page get rules per URL prefix. For example, Yahoo Finance hosts
syndicated press releases, which are primary, and algorithmic articles, which are weak. Official company
accounts on X, Facebook and Instagram are primary, while other social posts are weak. A source with no rule
is an error, not a default, so nothing is classified by accident. Where a source could fairly sit in two
tiers, the lower one is used.

**Result:** 5,369 evidence records across 11 runs, from 306 distinct URLs, all classified:

| | Distinct URLs | primary | top_secondary | weak |
|---|---|---|---|---|
| All research, both implementations | 306 | 102 (33%) | 28 (9%) | 176 (58%) |
| CrewAI research (4 full runs) | 230 | 93 | 11 | 126 |
| n8n research (1 run) | 153 | 36 | 22 | 95 |

Most of what the searches return is weak. The final documents cite a better mix than that (see KPI 2), so
the Analyst is selecting upward, but it can only choose from what research found.

**Judgment calls worth reviewing** (each is one line in the YAML):

- BroadbandNow, BBB and Reddit are on the brief's preferred list but are classified weak. BroadbandNow
  pages are marketing summaries of FCC data, not the data. BBB and Reddit are unverified individual reports.
  They are still the right evidence for customer pain points (RQ6), and the tiers penalize them anyway.
- Deal guides on Business Insider, The Hollywood Reporter and Rolling Stone are affiliate content, so weak,
  even though these are otherwise reputable publishers.
- Spectrum News is classified top_secondary (bylined news), but Charter owns it, which is a conflict of
  interest when it reports on Spectrum.

---

## KPI 1: Research coverage (target ≥ 90%)

**Definition.** The share of the brief's 8 research questions for which the final document cites at least
one source that was retrieved for that question.

**Data collected.** For each run: the evidence set (every record carries the research question it was
retrieved for) and the delivered document (`05_document.md` for CrewAI; for n8n, the Google Doc as n8n read
it back after writing, `eval/inputs/n8n_client-mudhx5in-ji99rx.md`).

**Calculation.** Collect the evidence IDs cited in the document. Map each one to the research questions its
search result served. One result is stored once per question it served, each copy with its own ID, so
citing any copy counts for all of those questions. Coverage = covered questions ÷ 8.

**Result.**

| | Runs | Coverage | Uncovered |
|---|---|---|---|
| CrewAI, full runs | 4 | 100% in every run | none |
| CrewAI, fixed-evidence A/B runs | 6 | 100% in every run | none |
| n8n | 1 | 50% | RQ3 (fees), RQ4 (switching triggers), RQ5 (bundle influence), RQ8 (channels) |

**Limitations.**
- n8n's document shows only the Strategy sections (a known renderer gap, `schemas/gtm_document_template.md`
  rule 4). Its Analyst artifact cites more evidence, which the document never shows. Coverage of the
  *analysis* would be higher; coverage of the *delivered document* is 50%.
- 130 of n8n's 400 evidence records have no research question attached, so they cannot count towards any
  question.
- "Covered" means at least one source. It does not mean the question was answered well. The rubric covers
  that.

---

## KPI 2: Source quality (target ≥ 80% top-tier, 0% broken links)

**Definition.**
- *Top-tier share:* the share of distinct sources cited in the final document that are primary or
  top_secondary.
- *Broken links:* the share of distinct cited sources whose link is broken (HTTP 404 or 410, or a host
  that doesn't resolve). This is a hard requirement of 0%.

**Counting rule.** The unit is the **distinct URL cited in the final document**. A page stored as several
evidence records (one per research question) counts once; otherwise one page could inflate the share. The
per-record share, which is the guide's example rule, is reported alongside and is usually within a few
points. Evidence that was collected but not cited does not count: the KPI is about what the reader is
shown.

**Data collected.** Cited evidence IDs from each document; each URL's tier from `source_tiers.yaml`; the
run-time link check (CrewAI's `06_link_check.json`); and a re-check of every cited URL today with the same
rules as the pipeline (HEAD request, retried as GET on 403 or 405).

**Calculation.** Top-tier share = (primary + top_secondary) ÷ distinct cited URLs. Broken share = broken
÷ distinct cited URLs. "Blocked" (403, 429 and similar: the site refuses automated checks) and
"unverified" (timeouts, 5xx errors) are reported separately and **not** counted as broken, because neither
shows the page is gone.

**Result.**

| Run | Cited URLs | Top-tier (by URL) | Top-tier (by record) | Broken at run time | Broken today | Blocked / unverified today |
|---|---|---|---|---|---|---|
| CrewAI run-20260920-223513 | 46 | 67% | 67% | 0 | 0 | 6 / 5 |
| CrewAI run-20260920-232205 | 30 | 73% | 74% | 0 | 0 | 2 / 4 |
| CrewAI run-20260921-102023 | 32 | 66% | 67% | 0 | 0 | 9 / 5 |
| CrewAI run-20260921-121832 (golden) | 49 | 69% | 74% | 0 | 0 | 6 / 5 |
| n8n client-mudhx5in-ji99rx | 11 | 82% | 77% | not checked | 0 | 1 / 2 |

The fixed-evidence A/B runs cite 58–75% top-tier sources: control 58–65%, strict-sources 61–75%.

CrewAI: top-tier share **not met** (66–73%); broken links **met** (0%). n8n: top-tier share **met on a
small base** (9 of 11 sources); broken links **0% today**, but not checked at run time.

**Limitations.**
- **The tier describes the source, not whether it supports the claim.** CrewAI's golden run cites primary
  pages for the wrong market (a Michigan MapQuest listing, Port St. Lucie and Palm Bay pages for Ocala
  claims). These count as primary or weak by type. Relevance is judged in the rubric, not here.
- **n8n's 82% rests on 11 sources.** One more weak source would drop it to 75%. Also, it has no evidence
  from Wire3 at all, so the share is high partly because it cites little.
- **The classification is judgment,** applied as rules. Changing a borderline rule, such as treating
  BroadbandNow as top_secondary, moves CrewAI by about 2 points per URL affected. The CSV makes each call
  auditable.
- **Links can die later.** "Broken today" is a 2026-09-23 snapshot. n8n's run-time link check did not run:
  its one `validate_source` call failed.
- **About 20–30% of cited links are blocked or unverified,** not confirmed working. The pages probably
  exist, since sites refuse automated checks, but a person should click them before the document is shared.

---

## KPI 3: Latency (target < 15 minutes)

**Definition.** Wall-clock time from pipeline start to the verified document, for one run of the brief.

**Data collected.** `duration_ms` from each run's `run_record` (`crewai/logs/runs.jsonl`,
`n8n/logs/runs.jsonl`), written by the pipeline itself.

**Calculation.** Per completed full run; the target is met when every completed run is under 15 minutes.
The maximum and the median are reported.

**Result.**

| | Completed runs | Minutes | Max | Median |
|---|---|---|---|---|
| CrewAI | 4 | 10.4, 11.6, 7.1, 8.4 | 11.6 | 9.4 |
| n8n | 1 | 7.7 | 7.7 | 7.7 |

Both **met**. Both are also inside the brief's own, stricter budget of 12 minutes.

**Limitations.**
- **CrewAI's full runs predate today's code changes.** Those added stricter grounding checks, which can
  add Analyst retries. The A/B runs on current code took 3.8–11.1 minutes for the Analyst and Strategy steps
  alone. The spread comes from the number of Analyst drafts (1 to 4), so a current full run could approach
  15 minutes.
- **Failed runs are excluded from latency:** CrewAI's failed run took 11.3 minutes; n8n's took 6.4 and 1.8.
  Reliability is reported below.
- **n8n has one data point.**

---

## KPI 4: Strategy quality (target ≥ 4 / 5)

**Definition.** The mean of the six rubric criteria in `eval/rubric.md` (clarity, feasibility,
differentiation, evidence quality, citation completeness, usefulness), each scored 1–5 against written
anchors. Met at a mean of 4.0 or more (24 or more out of 30).

**Data collected.** Review files in `eval/reviews/`. Each score needs one line of evidence.

**Calculation.** The mean of the six scores per document, then the median across reviewers and runs. Only
**human, blind** reviews count towards the KPI. Model reviews are listed but never counted.

**Result.** **Not measured yet: there is no human review.** The first pass by a model (not blind, one run
each) gives CrewAI 3.33 (20/30) and n8n 2.33 (14/30). If a human review agrees, neither would meet the
target.

**Limitations.** A model scoring model output is biased and not blind. One run per implementation. Rubric
scores vary by reviewer; the rubric asks for two reviewers and a discussion when they differ by 2 or more.
The 6 blinded A/B documents (`eval/ab/analyst-sources/packets/`) can be scored with the same rubric and
would give CrewAI a 6-run KPI 4 figure.

---

## KPI 5: Reproducibility (target ≥ 80% consistency)

**Definition.** How often repeated runs of the same brief reach the same answers on the decisions a reader
would act on. Wording is excluded: two good runs never share wording, so text similarity would measure the
wrong thing.

**Data collected.** 17 decision items from each run's Analyst and Strategy artifacts:
- for each of the 4 companies: footprint confirmed, service type, mobile bundle available (12 items);
- for each of the 3 competitors: the set of priced plans as (promo price, post-promo price) (3 items);
- the set of recommended channel categories, and the number of ideal customer profiles (2 items).

**Calculation.** For each item, the share of runs that give the most common answer. Consistency is the mean
across the 17 items: 100% means every run agrees on everything. It needs at least 3 runs. Two figures are
reported:
- *End to end:* full runs, so research varies too.
- *Fixed evidence:* the A/B control runs, which share one evidence set and the committed prompts. This
  isolates the Analyst and Strategy steps.

**Result.**

| | Runs | Consistency | Least consistent items |
|---|---|---|---|
| CrewAI, end to end | 4 | **88%** | T-Mobile plans (25%), Spectrum plans (50%), AT&T plans (50%), channel categories (75%) |
| CrewAI, fixed evidence | 3 | **92%** | Spectrum plans (33%), AT&T plans (67%), number of ICPs (67%) |
| n8n | 1 | cannot be measured | |

CrewAI **met**. The company facts are fully consistent. The **pricing matrix is the unstable part**: runs
pick different plans and price points, even from the same evidence.

**Limitations.**
- The item list is a choice. More pricing items would lower the score; more company facts would raise it.
- Exact-match pricing is strict: a $60 and a $59.99 promo count as disagreement.
- 3–4 runs is a small sample.
- CrewAI's end-to-end runs span two days of code changes.
- n8n needs at least 3 completed runs.

---

## KPI 6: Cost within budget (≤ $2.50 per run)

**Definition.** Estimated LLM cost of one full run, against the brief's `max_cost_usd` of $2.50.

**Data collected.** `cost.estimated_llm_usd` from each run record.
- CrewAI: calculated from provider-reported token counts per call, at the gpt-5-mini prices in
  `crewai/wire3_gtm/run_log.py` (checked 2026-09-19).
- n8n: estimated from the token counts n8n shows.

**Calculation.** Per completed run; met when every run is at or under $2.50.

**Result.**

| | Completed runs | Cost per run | Max | Share of budget |
|---|---|---|---|---|
| CrewAI | 4 | $0.11, $0.20, $0.13, $0.15 | $0.20 | 8% |
| n8n | 1 | ≥ $0.35 | ≥ $0.35 | ≥ 14% |

Both **met**, by a wide margin.

**Limitations.**
- **n8n's figure is a lower bound.** n8n cannot see gpt-5-mini's hidden reasoning tokens, which are billed
  as output. CrewAI's figure includes them.
- **Neither figure includes search-provider fees** (Tavily). CrewAI's 4 runs made 13–18 search calls each, n8n's
  run 26 (plus 1 link check). The per-call fee depends on the Tavily plan and isn't recorded.
- **Prices change.** The prices are a dated snapshot.
- **Failed runs cost money too:** CrewAI's failed run cost $0.09, n8n's failed runs $0.10 and $0.19.

---

## Reliability (context for all six)

| | Attempted | Completed | Failed | Notes |
|---|---|---|---|---|
| CrewAI | 5 | 4 | 1 | the failure was an invented evidence ID, since fixed; budget-cap tests excluded |
| n8n | 3 | 1 (degraded) | 2 | the completed run had 11 invented IDs removed |

## What would complete the comparison

1. **3 or more completed n8n runs of the current brief.** This makes KPI 5 measurable and gives KPIs 1–3
   and 6 more than one data point. Roughly $0.35 or more and 8 minutes per run.
2. **A blind human rubric review of both documents** (KPI 4), and of the 6 A/B packets for CrewAI.
3. **The n8n renderer fix** (template rule 4). It would change n8n's KPI 1 result the most, since the
   document would then show its analysis sections.
4. **For CrewAI's KPI 2:** research that retrieves more primary sources. The Analyst can't cite what the
   search didn't return, and 55% of the distinct sources CrewAI's research returned were weak.
