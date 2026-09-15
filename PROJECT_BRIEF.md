# Project Brief: Wire3 Residential Switcher GTM (Ocala, FL)

## 1. Product / company being analyzed
Wire3 — a regional fiber-to-the-home (FTTH) internet service provider. Product in scope: Wire3's residential fiber internet plans (standalone internet only; **no bundled mobile phone plan**, unlike the named competitors).

## 2. Target region and market segment
- **Region:** Ocala, FL / Marion County, FL — within Wire3's existing or planned fiber footprint in that area.
- **Segment:** Residential **price/value switchers** — households currently subscribed to a competitor who are cost-sensitive and price-comparison-driven (as opposed to segments primarily driven by brand loyalty, bundling convenience, or gaming/enterprise-grade needs). This segment is prioritized because it is the most winnable group for a challenger with lower price/promo levers but no mobile bundle or brand recognition to compete on.

## 3. Customer problem and business question
**Customer problem:** Ocala-area households on Spectrum, AT&T, or T-Mobile Home Internet routinely face promotional-rate cliffs, rising post-promo bills, equipment/hidden fees, and (for T-Mobile Home Internet) variable fixed-wireless speed/reliability — but see little reason to trust an unfamiliar local provider enough to switch.

**Business question:** Given that Wire3 cannot compete on mobile bundling or brand recognition, what go-to-market strategy — positioning, pricing/promo structure, messaging, and channels — will convert price/value-motivated residential switchers in Ocala/Marion County away from Spectrum, AT&T, and T-Mobile Home Internet, within a defined time and cost budget?

## 4. Candidate competitors
1. **Charter Spectrum** (cable — assumed incumbent; confirm local footprint)
2. **AT&T** (DSL and/or fiber where available; confirm Ocala-specific footprint — do not assume statewide AT&T fiber presence applies locally)
3. **T-Mobile Home Internet** (fixed wireless 5G; nationally available, footprint/speed varies by tower congestion)

*(Local footprint and overlap with Wire3's serviceable area is itself a research question — see RQ1.)*

## 5. Research questions (must each resolve to evidence + a source link)
1. What is each competitor's actual serviceable footprint and advertised speed tiers in Ocala/Marion County specifically? *(FCC Broadband Map, provider serviceability checkers)*
2. What are current standalone and bundled (internet+TV+mobile) pricing tiers, promo rates, and **post-promo rates** for each competitor? *(provider pricing pages, archived promo pages via Wayback Machine)*
3. What early-termination fees, equipment/rental fees, and other recurring surcharges does each competitor charge?
4. What documented triggers cause residential customers to switch ISPs (promo-cliff bill shock, reliability complaints, customer service failures, moving) — ideally Florida/regional evidence, national studies as fallback?
5. How much does a bundled mobile plan actually influence purchase decisions for price/value switchers specifically, vs. other segments? *(industry consumer research, e.g. Parks Associates, CivicScience, J.D. Power)*
6. What do verified customer reviews/complaints say about each competitor's pain points, prioritizing Ocala/central-FL sources, with statewide/national as fallback? *(BBB, Trustpilot, Google reviews, Reddit)*
7. How have other regional fiber ISPs without a mobile bundle successfully positioned and priced against national cable/wireless incumbents? *(trade press case studies — Fierce Telecom, Light Reading, press releases)*
8. What marketing channels are effective and cost-appropriate for a low-brand-recognition regional ISP in a mid-size FL market (vs. national ad channels the competitors can outspend)?

## 6. Required output sections (defines a "useful GTM plan")
1. Executive summary
2. Research scope & evidence table (linked to sources)
3. Competitor comparison table (Wire3 vs. the three competitors: price, speed, fees, contract terms, bundle availability)
4. Pricing matrix (standalone vs. bundled, promo vs. post-promo, by competitor)
5. Ideal customer profile(s) for the price/value switcher segment
6. Customer pains and desired outcomes
7. Value proposition and positioning (must explicitly address the no-mobile-bundle gap and the low-brand-recognition gap — not ignore them)
8. Message pillars
9. Recommended channels (must fit a regional/local budget, not a national-brand budget)
10. Launch phases and activities
11. Success metrics
12. Risks, assumptions, and open follow-up research questions
13. Citations / evidence appendix (every factual claim traceable to a source)

## 7. Time and API budget
**Per-run system budget (the agent pipeline, brief → drafted GTM doc):**
- Max wall-clock latency: 12 minutes
- Max external search/API calls: 40 per run
- Max LLM calls: 6 per agent role (Head Planner, Research, Analyst, Strategy)
- Max cost per run: $2.50 (search API + LLM tokens combined); pipeline must stop or clearly degrade (partial output + flagged gaps) if exceeded, not silently overrun.

**Project-level budget (building/testing both implementations against this brief):**
- Research/design time for this brief and artifact contract: already spent, capped at what's done.
- Build + test time: target ≤ 2 focused work sessions per implementation (n8n, CrewAI) before comparison.

## 8. Sources: preferred / excluded
**Preferred:** provider websites (spectrum.com, att.com, t-mobile.com/home-internet) and their public pricing/promo/terms pages; FCC National Broadband Map; BroadbandNow; J.D. Power / ACSI published results; Parks Associates / industry survey summaries that are publicly readable; BBB and Trustpilot; Reddit (r/Ocala, r/Florida, r/ISP, r/fiber); local news (Ocala Star-Banner); trade press (Fierce Telecom, Light Reading).

**Excluded:** anything requiring login/paywall bypass or scraping behind authentication; competitor internal/non-public data; Wire3 non-public internal data (CRM, sales pipeline, churn, CAC, subscriber counts, unannounced roadmap) — see academic-boundary note below.

## 9. Academic / hypothetical boundary (because this brief names a real employer)
- Only **Wire3 facts that are on Wire3's own public website** (public plans, pricing, coverage map) may be used as real inputs. Anything else about Wire3 — internal financials, churn, CAC, penetration, unannounced plans — is **out of scope** and must not be fabricated or guessed.
- All competitor data must come from public sources only, never confidential or scraped-behind-auth data.
- The GTM plan this system produces (pricing recommendations, promo structure, ad spend, channel mix) is an **academic strategy exercise for the capstone**, not an actual Wire3 business decision — outputs should be framed as hypothetical/illustrative recommendations grounded in public evidence, not represented as Wire3's real go-to-market plan.
- If the pipeline cannot verify a specific Wire3-vs-competitor footprint overlap in Ocala from public sources, it should say so explicitly rather than assume overlap.
