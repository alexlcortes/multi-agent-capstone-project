# Capstone KPIs: n8n vs CrewAI

Measured 2026-09-23 for brief `brief-d516e1daddfb` (the current `brief.json`), with **both implementations on
their current code**: 3 fresh CrewAI full runs, and every run of n8n's final workflow (per-company research,
published 2026-09-23). Every number is recomputed by one command:

```
cd crewai && uv run python -m wire3_gtm kpi --recheck-links
```

It writes `eval/kpi/results.json` (all figures below) and `eval/kpi/evidence_tiers.csv` (every evidence
record, its tier, and the rule that assigned it). The calculation is `crewai/wire3_gtm/kpi.py` (counting
rules pinned by `crewai/tests/test_kpi.py`); which runs count is `eval/kpi/runs.yaml`; n8n runs are exported
from n8n's database by `n8n/scripts/export_run.js`. Why the n8n workflow changed during the day, and which
differences are about the orchestrators themselves, is in `eval/COMPARISON_LOG.md`.

## Summary

| KPI | Target | CrewAI (3 runs) | n8n (3 of 4 runs completed) |
|---|---|---|---|
| 1. Research coverage | ≥ 90% | **100%** in every run: met | **100%** in every run: met |
| 2a. Top-tier sources | ≥ 80% | **58–69%**: not met | **68–73%**: not met |
| 2b. Broken links | 0% | **0%**: met | **0%** (checked today only): met |
| 3. Latency | < 15 min | **6.0–9.3 min**: met | **7.2–8.8 min**: met |
| 4. Strategy quality | ≥ 4 / 5 | **4.83** (provisional): met | **4.61** (provisional): met |
| 5. Reproducibility | ≥ 80% | **92%**: met | **78%**: not met (≈90% if wording is normalized, see KPI 5) |
| 6. Cost within budget | ≤ $2.50 / run | **$0.12–0.18**: met | **≥ $0.10–0.11** (lower bound): met |
| Reliability (context) | | **3 of 3** completed | **3 of 4** completed; all 3 had invented ids removed |

**In one paragraph:** on current code the two implementations are close on coverage, latency and cost, and
neither reaches the 80% top-tier-source target. CrewAI is more reliable (3 of 3 runs, against 3 of 4) and more
consistent from run to run (92% against 78%). n8n's completed runs cite a slightly better mix of sources. A
blind human review rates both above the strategy-quality target (CrewAI 4.83, n8n 4.61), but that result is
provisional: the scores have no evidence lines yet (see KPI 4).

**Which runs count** (`eval/kpi/runs.yaml`):
- **CrewAI:** full runs of the current brief started on or after 2026-09-23 17:06 UTC:
  `run-20260923-130640`, `-131659`, `-132404`. The 4 runs of 2026-09-20/21 predate the day's fixes and are not
  counted. The 6 A/B runs reuse one run's research, so they are used only for the "fixed evidence"
  reproducibility figure.
- **n8n:** runs of the final workflow, started on or after 2026-09-23 16:23 UTC: executions 35, 37 and 38
  completed, execution 36 failed. Earlier executions (29, 33) ran older workflow versions and are listed as
  superseded, never counted.
- Budget-cap tests (runs with a deliberately lowered budget) are excluded everywhere.

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

**How:** `eval/kpi/source_tiers.yaml` has a rule for every domain seen, each with its reason. Domains that host
several kinds of page get rules per URL prefix. For example, Yahoo Finance hosts syndicated press releases,
which are primary, and algorithmic articles, which are weak. Official company social accounts are primary;
other social posts are weak. A source with no rule is an error, not a default. Where a source could fairly
sit in two tiers, the lower one is used. **The tier describes the kind of source, not whether it supports the
claim:** a government page about the wrong company is still primary. Relevance is judged by the rubric.

**Result:** 5,421 evidence records from the counted runs, 203 distinct URLs, all classified. Across the
research as a whole, most sources are weak; the Analyst in each implementation cites a better mix than it was
given (see KPI 2).

**Judgment calls worth reviewing** (each is one line in the YAML):
- BroadbandNow, BBB and Reddit are on the brief's preferred list but are classified weak. BroadbandNow pages
  are marketing summaries of FCC data, not the data. BBB and Reddit are unverified individual reports. They
  are still the right evidence for customer pain points (RQ6), and the tiers penalize them anyway.
- Deal guides on Business Insider, The Hollywood Reporter and Rolling Stone are affiliate content, so weak.
- Spectrum News is classified top_secondary (bylined news), but Charter owns it.

---

## KPI 1: Research coverage (target ≥ 90%)

**Definition.** The share of the brief's 8 research questions for which the final document cites at least
one source that was retrieved for that question.

**Data collected.** Each run's evidence set (every record carries the research question it was retrieved for)
and its delivered document: CrewAI's `05_document.md`, and for n8n the Google Doc as n8n read it back after
writing (exported to `eval/kpi/data/n8n_<run>.md`).

**Calculation.** Collect the evidence ids cited in the document. Map each to the research questions its search
result served: one result is stored once per question, each copy with its own id, so citing any copy counts
for all. Coverage = covered questions ÷ 8.

**Result.** 100% in every counted run of both implementations (and in all 6 A/B runs).

**Limitations.** "Covered" means at least one cited source, not that the question was answered well; the rubric
judges that. Before the day's fixes, n8n scored 50% (execution 29), because its document showed only the
Strategy sections and 130 of its evidence records had no research question (`COMPARISON_LOG.md` #5, #6).

---

## KPI 2: Source quality (target ≥ 80% top-tier, 0% broken links)

**Definition.**
- *Top-tier share:* the share of distinct sources cited in the final document that are primary or
  top_secondary.
- *Broken links:* the share of distinct cited sources whose link is broken (HTTP 404 or 410, or a host that
  doesn't resolve). This is a hard requirement of 0%.

**Counting rule.** The unit is the **distinct URL cited in the final document**, so a page stored as several
evidence records counts once. The per-record share (the guide's example rule) is reported alongside.
Collected but uncited evidence does not count.

**Data collected.** Cited ids from each document; each URL's tier; CrewAI's run-time link check
(`06_link_check.json`); and a re-check of every cited URL on 2026-09-23 using the pipeline's own rules (HEAD
request, retried as GET on 403/405).

**Calculation.** Top-tier share = (primary + top_secondary) ÷ distinct cited URLs. Broken share = broken ÷
distinct cited URLs. "Blocked" (403, 429 and similar: the site refuses automated checks) and "unverified"
(timeouts, 5xx) are reported but **not** counted as broken, because neither shows the page is gone.

**Result.**

| Run | Cited URLs | Top-tier (by URL) | Top-tier (by record) | Broken, run time | Broken, today | Blocked / unverified today |
|---|---|---|---|---|---|---|
| CrewAI run-20260923-130640 | 43 | 58% | 62% | 0 | 0 | 11 / 5 |
| CrewAI run-20260923-131659 | 40 | 65% | 66% | 0 | 0 | 10 / 5 |
| CrewAI run-20260923-132404 | 35 | 69% | 69% | 0 | 0 | 7 / 4 |
| n8n execution 35 | 33 | 73% | 74% | not checked | 0 | 6 / 5 |
| n8n execution 37 | 39 | 72% | 78% | not checked | 0 | 7 / 5 |
| n8n execution 38 | 41 | 68% | 72% | not checked | 0 | 7 / 4 |

Top-tier share: **not met by either** (CrewAI 58–69%, n8n 68–73%). Broken links: **met by both** (0%). The 6
CrewAI A/B runs, on one fixed evidence set, cite 58–75% top-tier sources.

**Limitations.**
- **The tier is the kind of source, not its relevance.** Earlier reviews found primary pages cited for the
  wrong market (a Michigan MapQuest listing, Port St. Lucie pages for Ocala claims). The rubric catches that;
  this KPI does not.
- **The limit is the research, not the Analyst.** Most retrieved sources are weak, and the Analyst can only
  choose among them. The A/B test of a stricter source-selection prompt (`eval/ab/analyst-sources/`) did not
  move the share much (control 50%, challenger 54%, medians). Reaching 80% likely needs searches aimed at
  official sites.
- **The classification is judgment,** applied as rules. Moving a borderline domain, such as BroadbandNow, to
  top_secondary would shift a run by about 2–3 points per URL affected. The CSV makes every call auditable.
- **Links can die later.** "Broken today" is a 2026-09-23 snapshot. n8n has no run-time check (its workflow
  does not run `validate_source` on the document's sources).
- **About 25–35% of cited links are blocked or unverified**, not confirmed working. The pages probably exist,
  but a person should click them before sharing the document.

---

## KPI 3: Latency (target < 15 minutes)

**Definition.** Wall-clock time from pipeline start to the verified Google Doc, for one run of the brief.

**Data collected.** `duration_ms` in each run's `run_record`, written by the pipeline itself. Both
implementations wrote and verified a Google Doc in every counted run, so the comparison is like for like.

**Calculation.** Per completed run; met when every completed run is under 15 minutes. Max and median reported.

**Result.**

| | Completed runs | Minutes | Max | Median |
|---|---|---|---|---|
| CrewAI | 3 | 9.3, 6.0, 7.6 | 9.3 | 7.6 |
| n8n | 3 | 7.2, 8.8, 7.6 | 8.8 | 7.6 |

Both **met**, and both are inside the brief's own, stricter budget of 12 minutes.

**Limitations.** Three runs each. n8n's research step includes three 15-second pauses between companies
(`COMPARISON_LOG.md` #9), about 45 seconds of each run. Most of the spread comes from how many attempts the
Analyst needs. Failed runs are excluded from latency (n8n's execution 36 took 7.7 minutes before failing).

---

## KPI 4: Strategy quality (target ≥ 4 / 5)

**Definition.** The mean of the six rubric criteria in `eval/rubric.md` (clarity, feasibility,
differentiation, evidence quality, citation completeness, usefulness), each scored 1–5 against written
anchors. Met at a mean of 4.0 or more (24/30).

**Data collected.** One blind human review of the 6 documents of the counted runs (3 per implementation),
2026-09-23, on the review page (https://claude.ai/artifact/7wdndi5KFHZxxkdwib5UxC). The documents were shuffled,
labelled Doc-01 to Doc-06, and given identical presentation (`python -m wire3_gtm review-packets`). Scores are in
`eval/reviews/kpi4/2026-09-23-alejandro.json`; the unblinding key stays out of git until the review is final.

**Calculation.** Mean of the six scores per document, then the mean across each implementation's documents.

**Result (provisional).**

| | Documents | Mean per document | Overall | Clarity | Feasibility | Differentiation | Evidence quality | Citations | Usefulness |
|---|---|---|---|---|---|---|---|---|---|
| CrewAI | 3 | 4.5, 5.0, 5.0 | **4.83** | 4.67 | 5.0 | 4.67 | 4.67 | 5.0 | 5.0 |
| n8n | 3 | 4.5, 4.67, 4.67 | **4.61** | 5.0 | 4.0 | 4.0 | 4.67 | 5.0 | 5.0 |

Both are above the 4.0 target. CrewAI scores higher on feasibility and differentiation; n8n higher on clarity.

**Why it is provisional:**
- **No evidence lines yet.** The rubric says a score without one line of evidence is not used; the reviewer
  will add them. Until then `kpi.py` marks these scores as not counting (`counts_toward_kpi: false`).
- **Some scores sit above the rubric's written anchors for the measured facts.** Evidence quality 5 requires
  ≥80% top-tier sources; every counted document measured 58–73% (KPI 2). Usefulness 5 requires a price
  recommendation; every document lists Wire3's standard prices as an unknown. Feasibility 5 requires spend sized
  against the budget; the documents give only low/medium tiers. The evidence pass is the place to reconcile these.
- **Near the ceiling:** 30 of 36 scores are 5, which leaves little room to separate the implementations.
- **One reviewer, who is also the project's author.** The rubric asks for two reviewers where possible.
- **Blinding is presentation-only:** CrewAI documents contain their own evidence-quality notes in section 2,
  so a reviewer may recognize them.

---

## KPI 5: Reproducibility (target ≥ 80% consistency)

**Definition.** How often repeated runs of the same brief reach the same answers on the decisions a reader
would act on. Wording is excluded: two good runs never share wording, so text similarity would measure the
wrong thing.

**Data collected.** 17 decision items from each run's Analyst and Strategy artifacts:
- for each of the 4 companies: footprint confirmed, service type, mobile bundle available (12 items);
- for each of the 3 competitors: the set of priced plans as (promo price, post-promo price) (3 items);
- the set of recommended channel categories, and the number of ideal customer profiles (2 items).

**Calculation.** For each item, the share of runs that give the most common answer; consistency is the mean
over the 17 items. 100% means every run agrees on everything. It needs at least 3 runs.

**Result.**

| | Runs | Consistency | Least consistent items |
|---|---|---|---|
| CrewAI, end to end | 3 | **92%** | T-Mobile plans (33%), Spectrum plans (67%), AT&T plans (67%) |
| CrewAI, fixed evidence (A/B control) | 3 | **92%** | Spectrum plans (33%), AT&T plans (67%), number of ICPs (67%) |
| n8n, end to end | 3 | **78%** | service type for Spectrum, AT&T and T-Mobile (33% each), Spectrum and T-Mobile plans (33%), channel categories (67%) |

CrewAI **met**; n8n **not met**.

**Why n8n's service type disagrees:** n8n's analysis schema allows free text for service type, so its runs
describe the same thing in different words ("cable (fiber-powered / HFC)", "hybrid (fiber & HFC)", "hybrid
(HFC/fiber)"). CrewAI's schema allows only cable, fiber, dsl or fixed_wireless. If n8n's wording is mapped
to those categories, its three service-type items agree fully and consistency is **about 90%**. That is
reported as a sensitivity check, not the result: the metric was fixed before the runs. The cause is a schema
choice in our n8n build, not a limit of n8n (`COMPARISON_LOG.md` #12).

**Both implementations are least consistent on pricing:** runs pick different plans and price points.

**Limitations.** The item list is a choice: more pricing items would lower both scores, more company facts
would raise them. Exact-match pricing is strict ($60 and $59.99 disagree). Three runs is a small sample. n8n
has no fixed-evidence figure (no seeded runs).

---

## KPI 6: Cost within budget (≤ $2.50 per run)

**Definition.** Estimated LLM cost of one full run, against the brief's `max_cost_usd` of $2.50.

**Data collected.** `cost.estimated_llm_usd` in each run record. CrewAI's is calculated from provider-reported
token counts per call. n8n's is estimated from the token counts n8n shows. Both use the gpt-5-mini prices
checked on 2026-09-19.

**Calculation.** Per completed run; met when every run is at or under $2.50.

**Result.**

| | Completed runs | Cost per run | Max | Share of budget |
|---|---|---|---|---|
| CrewAI | 3 | $0.18, $0.12, $0.14 | $0.18 | 7% |
| n8n | 3 | ≥ $0.10, ≥ $0.11, ≥ $0.11 | ≥ $0.11 | ≥ 4.5% |

Both **met**, by a wide margin.

**Limitations.**
- **n8n's figure is a lower bound.** n8n cannot see gpt-5-mini's hidden reasoning tokens, which are billed as
  output; CrewAI's figure includes them. The two are therefore **not** directly comparable, and n8n's true cost
  is likely similar to or above CrewAI's.
- **Neither includes search-provider fees** (Tavily). CrewAI's counted runs made 15–18 searches each, n8n's 16–20.
- **Failed runs cost money too:** n8n's execution 36 cost at least $0.12 and produced no document.
- **Prices are a dated snapshot.**

---

## Reliability (context for all six)

| | Attempted on counted version | Completed | Notes |
|---|---|---|---|
| CrewAI | 3 | 3 | its own checks flagged unsupported numbers (4–9 per run) and 1 invented id, all handled in-run |
| n8n | 4 | 3 (all "degraded") | 15, 2 and 17 invented evidence ids removed; execution 36 failed on one self-referencing citation |

The difference is mostly an orchestrator finding: CrewAI re-prompts the model inside the task when a check
fails; n8n checks after the model step and can only remove the bad citation or stop the run
(`COMPARISON_LOG.md` #10, #11).

## What would complete the comparison

1. **A blind human rubric review** of both implementations' current documents (KPI 4). This is the only KPI
   still unmeasured, and the one closest to the plan's real value.
2. **More runs** would firm up reproducibility and reliability: three is the minimum, not a robust sample.
3. **Top-tier sources (both):** research aimed at official sites. The Analyst prompt alone did not move it
   (A/B test).
4. **n8n reproducibility:** a fixed set of values for service type in its analysis schema, as CrewAI has.
