# Project Brief: Wire3 Residential Value & Affordability GTM (Lake County, FL — Leesburg / Mount Dora)

> **Status: runnable in CrewAI, not yet run.** Added after the capstone submission (2026-09-24). CrewAI only. Machine-readable version: [`brief.json`](brief.json). See [`README.md`](README.md) in this folder for how to run it and how each required section is produced. The original, submitted brief is [`../../PROJECT_BRIEF.md`](../../PROJECT_BRIEF.md) (Ocala).

## 1. Product / company being analyzed
Wire3 — a regional fiber-to-the-home (FTTH) internet service provider. Product in scope: Wire3's residential fiber internet plans as published on Wire3's public website (standalone internet only; **no bundled mobile phone plan or TV bundle**, unlike the primary competitors).

## 2. Target region and market segment
- **Region:** Lake County, FL — specifically **Leesburg and Mount Dora**, within Wire3's existing or recently constructed fiber footprint.
- **Geography definition:** "Leesburg" and "Mount Dora" mean different populations depending on the boundary used. The pipeline must report **city-limit (Census place)** figures and **ZIP-area (ZCTA)** figures side by side, and label which one each claim uses: Leesburg city + ZIPs 34748 and 34788; Mount Dora city + ZIP 32757.
- **Market context (verified baseline: U.S. Census ACS 2020–2024 5-year estimates via the Census Data API, retrieved 2026-09-24; the pipeline must re-pull these rather than copy them):**

  | Area | Households | Median HH income | Renters | Poverty | Median age | Homes in 2+ unit buildings | Mobile homes | Broadband subscription |
  |---|---|---|---|---|---|---|---|---|
  | Leesburg (city) | 12,833 | $52,880 | 37% | 16.4% | 46.2 | 31% | 9% | 91% |
  | ZIP 34748 (Leesburg) | 21,672 | $58,192 | 25% | 13.1% | 59.8 | 19% | 15% | 92% |
  | ZIP 34788 (Leesburg) | 9,522 | $54,147 | 16% | 11.0% | 56.4 | 4% | 51% | 91% |
  | Mount Dora (city) | 7,646 | $74,537 | 38% | 11.8% | 48.8 | 29% | 3% | 94% |
  | ZIP 32757 (Mount Dora) | 14,050 | $77,216 | 26% | 10.8% | 45.8 | 17% | 6% | 93% |
  | Lake County | 164,497 | $73,161 | 22% | 8.9% | 46.5 | 13% | 16% | 93% |

  - **Leesburg city limits** have lower income, higher poverty, and more renters and apartments than the county (the cost-constrained profile).
  - **Leesburg ZIP areas outside city limits** are older and mostly owner-occupied. ZIP 34788 is majority manufactured housing, so manufactured-home communities are a primary channel there, not an edge case.
  - **Mount Dora** is higher-income and older, but its city limits are 38% renters, so "high homeownership" does not hold inside the city.
  - Provider presence is **unverified**. The working hypothesis is a **cable incumbent (Xfinity/Comcast)**, legacy **copper DSL (CenturyLink)**, pockets of **Quantum Fiber** and **Spectrum**, and fixed wireless (T-Mobile, Verizon) throughout. RQ1 must confirm or reject each.
  - Wire3's fiber is **new to much of this area**, so low brand awareness and "who is Wire3?" skepticism should be treated as a central barrier.
- **Segment:** **Budget-conscious and value-seeking residential households**, in two sub-segments the plan must address separately:
  - **Segment rule:** Assign households by their **primary barrier to switching**, not by age or income source. A fixed-income or retired household belongs to whichever segment matches its primary barrier.
  1. **Cost-constrained households.** The barrier is affording to get or stay connected: monthly price, upfront costs (install, equipment, deposits), credit checks, and contract lock-in. Commonly renters, multifamily residents, and households affected by the end of the federal Affordable Connectivity Program.
  2. **Value switchers.** Already subscribed; the barrier is switching friction and trust in a new provider, and the trigger is the bill increase after promotional pricing expires. Commonly homeowners, including retirees and manufactured-home owners. They are price-comparison-driven but also value reliability and simple, predictable billing.
  - **Sizing:** The Census does not measure primary barrier. Segment sizes must be estimated from public proxies (poverty rate, renter share, income) and labeled as estimates.
- **Framing requirement:** Describe neighborhoods and households only in terms of **publicly measurable, economic, and connectivity characteristics** (income, housing type, current provider options, speeds, affordability programs). Do **not** characterize areas by crime, safety, or other stigmatizing descriptors. The plan must not recommend excluding, deprioritizing, or offering worse terms to areas based on income or demographic composition.

## 3. Customer problem and business question
**Customer problem:** Lake County households on Xfinity, CenturyLink, Quantum Fiber, Spectrum, or fixed-wireless internet face these problems:
- promotional-rate cliffs and rising post-promo bills
- equipment and "hidden" fees
- data caps (where applicable) and slow copper DSL speeds
- for cost-constrained households, upfront costs and credit or deposit barriers

Many also have little reason to trust an unfamiliar local provider that has only recently arrived.

**Business question:** Wire3 cannot compete on mobile or TV bundling or on brand recognition. Given that, what go-to-market strategy will convert budget-conscious and value-seeking households in Leesburg and Mount Dora away from Xfinity, CenturyLink, Quantum Fiber, Spectrum, T-Mobile Home Internet, and Verizon Home Internet, within a defined time and cost budget? The strategy should cover positioning, pricing and promo structure, affordability options, messaging, and channels.

## 4. Candidate competitors
1. **Xfinity (Comcast)**: cable, the presumed dominant incumbent across most of the target area. Include its low-income program (Internet Essentials) and prepaid option (Xfinity NOW) in scope.
2. **CenturyLink (Lumen)**: legacy copper DSL across much of the area.
3. **Quantum Fiber**: fiber in some neighborhoods. Research the current ownership and branding of the Quantum Fiber consumer business (it may now sit under AT&T); do not assume.
4. **Spectrum (Charter)**: present in parts of Mount Dora. Confirm the footprint.
5. **T-Mobile Home Internet**: fixed wireless, likely available across the region, with performance that varies with tower congestion.
6. **Verizon Home Internet**: fixed wireless (5G/LTE), likely available across the region, with performance that varies with tower congestion.

Each competitor is its own row in every comparison table and pricing matrix. With Wire3, that is **7 rows**: Wire3, Xfinity, CenturyLink, Quantum Fiber, Spectrum, T-Mobile Home Internet, Verizon Home Internet.

*(Local footprint and overlap with Wire3's serviceable area is itself a research question. See RQ1.)*

## 5. Research questions (must each resolve to evidence + a source link)
1. What is each competitor's actual serviceable footprint and advertised speed tiers in Leesburg and Mount Dora specifically, and where does each overlap with Wire3's publicly listed coverage? *(FCC Broadband Data Collection provider-availability data, BroadbandNow city pages, Wire3 public coverage and news pages)*
2. What are each competitor's current standalone and bundled pricing tiers, promo rates, and **post-promo rates**? *(provider pricing and terms pages; third-party pricing summaries such as BroadbandNow)*
3. What upfront and recurring costs does each competitor charge? Include install fees, equipment and rental fees, deposits or credit-check requirements, early-termination fees, data caps and overage charges, and other surcharges.
4. What low-income, prepaid, or no-contract options does each competitor offer (e.g., Xfinity Internet Essentials, Xfinity NOW, Lifeline-eligible plans)? What are their eligibility rules, prices, and speeds? What has changed for affected households since the Affordable Connectivity Program ended?
5. What public demographic and housing data describes each target city? Report city-limit and ZIP-area (ZCTA) figures side by side (see Section 2). Cover median household income, poverty rate, share of renters vs. owners, share living in multifamily housing and manufactured homes, age profile, household broadband adoption rates, and primary languages spoken at home. *(U.S. Census ACS 5-year estimates via the Census Data API)*
6. What documented triggers cause residential customers to switch ISPs, such as promo-cliff bill shock, reliability complaints, customer service failures, or moving? Florida or regional evidence is preferred, with national studies as a fallback.
7. How much do bundles (mobile, TV) influence purchase decisions for budget-conscious households compared with other segments? *(industry consumer research, e.g. Pew Research, J.D. Power press releases, CivicScience public summaries)*
8. What do verified customer reviews and complaints say about each competitor's pain points? Prioritize Lake County and Central Florida sources, with statewide or national as a fallback. *(BBB, Trustpilot, Reddit, local news)*
9. How have other regional fiber ISPs without a mobile bundle positioned and priced against a cable incumbent? Include how they have reached price-sensitive and new-to-fiber neighborhoods, such as affordable tiers, community partnerships, and apartment and property-manager programs. *(Fierce Network, Light Reading, Broadband Communities, press releases, NTIA/BEAD digital-equity case studies)*
10. Which marketing channels are effective and cost-appropriate for a low-awareness regional ISP entering Lake County neighborhoods? Consider community organizations, libraries, churches and civic groups, property managers of apartments and manufactured-home communities, local events, local radio and print, direct mail, digital/geo-targeted ads, and referral programs. Compare these with national ad channels the incumbents can outspend.
11. What local organizations are active in digital inclusion or affordability in Lake County that could be credible partners? Examples include Lake County library system programs, digital navigator programs, and Florida's broadband office initiatives.

## 6. Required output sections (defines a "useful GTM plan")
1. Executive summary
2. Research scope & evidence table (linked to sources)
3. Market profile by city: demographic, housing, and broadband-adoption snapshot, using public data only
4. Competitor comparison table: Wire3 vs. competitors on price, speed, fees, upfront costs, contract terms, data caps, bundle availability, and low-income/prepaid options
5. Pricing matrix: standalone vs. bundled, promo vs. post-promo, and standard vs. low-income/prepaid, by competitor
6. Ideal customer profiles, at least one for each sub-segment (cost-constrained households; value switchers)
7. Customer pains and desired outcomes
8. Value proposition and positioning. Must explicitly address the **no-bundle gap**, the **low-brand-recognition / new-provider trust gap**, and the **upfront-cost/affordability barrier**.
9. Message pillars, including guidance on respectful, non-stigmatizing, plain-language messaging and on bilingual materials if the evidence supports it
10. Recommended channels, which must fit a regional/local budget and include community and partnership channels
11. Launch phases and activities
12. Success metrics, including awareness and trust indicators and not only sales conversion
13. Equity and compliance check: confirm the recommendations do not treat neighborhoods differently in availability, pricing, or service quality based on income or demographics, and note relevant consumer-protection and digital-discrimination considerations
14. Risks, assumptions, and open follow-up research questions
15. Citations / evidence appendix (every factual claim traceable to a source)

## 7. Time and API budget
**Per-run system budget (the agent pipeline, from brief to drafted GTM doc):**
- Max wall-clock latency: 20 minutes
- Max external search/API calls: 60 per run. One-time cached data pulls (the FCC availability extract, Census tables) are prepared before the run and do not count against this cap.
- Max LLM calls: 8 per agent role (Head Planner, Research, Analyst, Strategy)
- Max cost per run: $2.50 (search API + LLM tokens combined). If this is exceeded, the pipeline must stop or clearly degrade (partial output + flagged gaps), not silently overrun.
- **Priority if budget runs short:** RQ1–RQ4 (footprint, pricing, fees, affordability programs) → RQ5 (market profile) → RQ9–RQ10 (analogues, channels) → remainder.

**Implementation scope:** This brief is run in the **CrewAI implementation only**. The n8n implementation will not be run against it, and no n8n-vs-CrewAI comparison will be made for Lake County.

**Project-level budget:** ≤ 2 focused work sessions on the CrewAI implementation.

## 8. Sources: preferred / excluded
**Preferred:**
- Provider public pricing, promo, terms, and low-income-program pages: xfinity.com, centurylink.com, quantumfiber.com, spectrum.com, t-mobile.com/home-internet, verizon.com/home/internet
- Government and public data: FCC Broadband Data Collection data downloads, U.S. Census ACS via the Census Data API, the FCC Lifeline program pages, Florida Office of Broadband, Lake County and city government sites
- Third-party comparisons and ratings: BroadbandNow; J.D. Power / ACSI published results; Pew Research and other publicly readable survey summaries
- Reviews and forums: BBB, Trustpilot, Reddit (r/lakecountyfl, r/Leesburg, r/orlando, r/Florida, r/ISP, r/fiber)
- News and trade press: local news (Daily Commercial, Orlando Sentinel, local TV); Fierce Network, Light Reading, Broadband Communities

**Excluded:**
- Anything requiring login or paywall bypass, or scraping behind authentication
- Interactive tools the pipeline cannot read through search: the FCC National Broadband Map web interface, address-based provider serviceability checkers, and data.census.gov (use the data downloads and APIs in Government and public data instead)
- Sources that produced no usable evidence or are paywalled: Google reviews, Wayback Machine archives, and Parks Associates reports
- Competitor internal or non-public data
- Wire3 non-public internal data, such as CRM, sales pipeline, take rate/penetration, churn, CAC, subscriber counts, internal market characterizations, or unannounced roadmap
- Crime statistics or crime-news sources as a basis for segmenting, targeting, or excluding neighborhoods

## 9. Academic / hypothetical boundary (because this brief names a real employer)
- Only **Wire3 facts on Wire3's own public website** may be used as real Wire3 inputs: public plans, pricing, coverage map, and any published affordability or low-income offering. Everything else about Wire3 is **out of scope** and must not be fabricated or guessed. That includes financials, take rate, churn, CAC, penetration, and unannounced plans.
- All competitor and market data must come from public sources only.
- The GTM plan is an **academic strategy exercise for the capstone**, not an actual Wire3 business decision. Pricing, promo, affordability, spend, and channel recommendations should be framed as hypothetical and illustrative, grounded in public evidence.
- If the pipeline cannot verify a specific Wire3-vs-competitor footprint overlap in the target cities from public sources, it must say so explicitly rather than assume overlap.
- If Wire3's public site does not list an affordability or low-income plan, the pipeline may recommend one as a hypothetical. It must not state or imply that one exists.
