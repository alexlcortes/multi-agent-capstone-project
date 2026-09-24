"""Executive brief: the same validated artifacts as the full GTM plan, cut down
for a CEO or other leader who will not work through the full document: the
recommendation, why we can win, plan, spend, metrics, risks.

No LLM and no new facts: every line is a field the Strategy or Analyst step
already produced and the guardrails already checked (same preflight as the full
plan). Bracketed ids are dropped from the body for readability; judgment calls
stay visible as "(judgment)", and the sources behind the included content are
listed at the end. The full plan for the same run remains the reference.
"""

from datetime import datetime, timezone

from wire3_gtm.analyst_models import AnalystArtifact, dump_contract
from wire3_gtm.docs_content import HTTP_URL_RE, DocumentPlan, _Builder, _natural, _text, census_records, preflight
from wire3_gtm.evidence import EvidenceRecord
from wire3_gtm.models import ResearchPlan
from wire3_gtm.strategy_models import StrategyArtifact

DISCLAIMER = (
    "Academic strategy exercise for the capstone. Prices, promotions, spend and targets here are illustrative, "
    "grounded in public evidence, and are NOT Wire3's actual pricing or go-to-market plan."
)
RANK = {"high": 3, "medium": 2, "low": 1}
SERVICE = {"fixed_wireless": "fixed wireless", "dsl": "DSL"}


def _money(f) -> str | None:
    v = f["value"] if f else None
    return None if v is None else f"${v:,.2f}".replace(".00", "")


def judged(*fields) -> str:
    return " (judgment)" if any(f and f.get("basis") == "inference" for f in fields) else ""


class _Brief:
    def __init__(self, strategy: dict, analyst: dict, evidence: list[EvidenceRecord]):
        self.st, self.an = strategy, analyst
        self.evidence = evidence
        self.by_id = {e.evidence_id: e for e in evidence}
        self.used: set[str] = set()
        # analyst finding id -> the evidence behind it, so strategy lines resolve to sources
        self.findings: dict[str, list[str]] = {}
        for t in analyst["market_themes"]:
            self.findings[t["theme_id"]] = t["supporting_evidence_ids"]
        for group in analyst["swot"].values():
            for s in group:
                self.findings[s["item_id"]] = s["evidence_ids"]
        for a in analyst["assumptions_unknowns_conflicts"]["assumptions"]:
            self.findings[a["id"]] = a["evidence_ids"]

    def plain(self, f) -> str:
        """A cited field's value as plain text; records the sources behind it."""
        if f is None:
            return "not found"
        for i in f.get("supporting_ids") or f.get("evidence_ids") or f.get("derived_from_evidence_ids") or []:
            self.used.update([i] if i.startswith("EV-") else self.findings.get(i, []))
        return "not found" if f["value"] is None else _text(f["value"])

    def v(self, f) -> str:
        """plain(), plus a visible flag when the value is a judgment call."""
        return self.plain(f) + judged(f)

    def price(self, f) -> str | None:
        m = _money(f)
        if m is not None:
            self.plain(f)
        return m and f"{m}/mo"

    def sources(self) -> list[EvidenceRecord]:
        """One entry per URL: several evidence records often quote the same page."""
        seen, out = set(), []
        for i in sorted(self.used, key=_natural):
            e = self.by_id.get(i)
            if e and e.source_url not in seen:
                seen.add(e.source_url)
                out.append(e)
        return out


def _competitor_lines(br: _Brief, b: _Builder) -> None:
    prices: dict[str, list] = {}
    for p in br.an["pricing_matrix"]:
        if p["post_promo_price_usd_per_month"]["value"] is not None:
            prices.setdefault(p["competitor_name"], []).append(p["post_promo_price_usd_per_month"])
    for r in br.an["competitor_comparison_table"]:
        name = r["competitor_name"]
        down, up = r["top_advertised_speed_mbps_down"], r["top_advertised_speed_mbps_up"]
        speed = f"{br.plain(down)}/{br.plain(up)} Mbps{judged(down, up)}"
        bundle = "has a mobile bundle" if r["has_mobile_bundle_available"]["value"] else "no mobile bundle"
        br.plain(r["has_mobile_bundle_available"])
        service = SERVICE.get(r["service_type"]["value"], br.plain(r["service_type"]))
        head = f"{'Wire3 (us)' if name == 'Wire3' else name}: "
        line = f"{service}, up to {speed}, {bundle}"
        if name in prices:
            line += f"; regular price from {br.price(min(prices[name], key=lambda f: f['value']))}"
        b.add(head + line + ".", "BULLET", bold_len=len(head))


def _executive(br: _Brief, b: _Builder, plan: ResearchPlan) -> None:
    st, an = br.st, br.an
    vp, pos = st["value_proposition"], st["positioning"]

    def recommendation():
        b.labelled("target", f"{plan.segment} in {plan.region}.", "P")
        b.labelled("value_proposition", br.v(vp["headline"]), "P")
        b.labelled("positioning", br.v(pos["positioning_statement"]), "P")

    def why():
        for d in pos["differentiators"]:
            b.add(br.v(d), "BULLET")
        b.add("Our gaps and how we answer them", "H3")
        b.labelled("no_mobile_bundle", br.v(vp["no_bundle_gap_response"]))
        b.labelled("low_brand_recognition", br.v(vp["low_brand_recognition_response"]))

    def market():
        for t in an["market_themes"]:
            br.used.update(t["supporting_evidence_ids"])
            b.add(t["title"], "BULLET")
        areas: dict[str, list] = {}
        for e in census_records(br.evidence):  # briefs with Census geographies only (not Ocala)
            topic = e.source_title.split(": ", 1)[-1].split(" (", 1)[0]
            if topic in ("income and poverty", "housing"):
                areas.setdefault(e.source_title.split(": ", 1)[0], []).append(e)
        if areas:
            b.add("Local market (U.S. Census, ACS 2020-2024)", "H3")
        for area, recs in areas.items():
            br.used.update(e.evidence_id for e in recs)
            lead = f"{area.replace('ZCTA5 ', 'ZIP area ')}: "
            b.add(lead + "; ".join(e.claim.split("): ", 1)[-1].rstrip(".") for e in recs) + ".", "BULLET",
                  bold_len=len(lead))

    def targets():
        for i in st["icps"]:
            lead = f"{i['name']}: "
            b.add(lead + br.v(i["description"]), "BULLET", bold_len=len(lead))

    def phases():
        for p in sorted(st["launch_phases"], key=lambda p: p["sequence_order"]):
            lead = f"{p['phase_name']} ({p['timeframe']}): "
            b.add(f"{lead}Finished when: {p['exit_criteria']}", "BULLET", bold_len=len(lead))

    def spend():
        b.add("The plan sets relative budget tiers, not dollar amounts.", "P")
        for tier in ("high", "medium", "low"):
            chans = [c["channel_name"] for c in st["channels"] if c["budget_tier"] == tier]
            if chans:
                lead = f"{tier.capitalize()} spend: "
                b.add(lead + "; ".join(chans), "BULLET", bold_len=len(lead))

    def metrics():
        for m in st["success_metrics"]:
            lead = f"{m['name']} ({m['measurement_cadence']}): "
            b.add(lead + br.v(m["target_value"]), "BULLET", bold_len=len(lead))

    def risks():
        ranked = sorted(st["risks"], key=lambda r: -RANK[r["likelihood"]["value"]] * RANK[r["impact"]["value"]])
        for r in ranked[:5]:
            lead = f"{r['likelihood']['value'].capitalize()} likelihood / {r['impact']['value']} impact: "
            b.add(f"{lead}{br.v(r['statement'])} Mitigation: {br.v(r['mitigation'])}", "BULLET", bold_len=len(lead))

    def questions():
        qs = sorted(st["follow_up_research_questions"], key=lambda q: -RANK[q["priority"]])
        for q in qs[:5]:
            lead = f"{q['priority'].capitalize()} priority: "
            b.add(lead + q["question"], "BULLET", bold_len=len(lead))
        if not qs:
            b.add("None recorded.", "P")

    b.section("The recommendation", recommendation)
    b.section("Why we can win", why)
    b.section("What the market looks like", market)
    b.section("Who we are targeting", targets)
    b.section("Competitive landscape", lambda: _competitor_lines(br, b))
    b.section("Plan and timing", phases)
    b.section("Where the money goes", spend)
    b.section("How we will know it is working", metrics)
    b.section("Top risks", risks)
    b.section("Open questions", questions)


def build_executive_brief(
    strategy: StrategyArtifact,
    analyst: AnalystArtifact,
    evidence: list[EvidenceRecord],
    plan: ResearchPlan,
    run_id: str,
    now: datetime | None = None,
) -> DocumentPlan:
    preflight(strategy, analyst, evidence)
    br = _Brief(dump_contract(strategy), dump_contract(analyst), evidence)
    b = _Builder()
    when = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")
    title = f"Wire3 Executive Brief - {run_id} - {when} UTC"

    b.add("Wire3 Go-To-Market: Executive Brief", "TITLE")
    b.add(f"Run: {run_id} | Generated: {when} UTC | Summarizes the full GTM plan from the same run, "
          f"which has every source and citation.", "P")
    b.add(DISCLAIMER, "P", bold_len=len(DISCLAIMER))
    _executive(br, b, plan)

    sources = br.sources()
    b.add("Sources behind this brief", "H2")
    b.add("\"(judgment)\" marks a recommendation with no direct source. Everything else traces to these:", "P")
    for e in sources:
        label = e.source_title or e.source_url
        ok = bool(HTTP_URL_RE.match(e.source_url or ""))
        b.add(label, "BULLET", link={"offset": 0, "length": len(label), "url": e.source_url} if ok else None)
    b.add(DISCLAIMER, "P", bold_len=len(DISCLAIMER))

    expected = {"title": title, "section_headings": [f"{i + 1}. {s}" for i, s in enumerate(b.sections)],
                "source_ids": [e.evidence_id for e in sources]}
    return DocumentPlan(title, b.blocks, b.sections, sources, expected)


def write_executive_brief(store) -> str:
    """Local Markdown only: runs/<run_id>/05_executive_brief.md. Reads the saved,
    validated artifacts, so it works on any finished run without new LLM calls."""
    from wire3_gtm.docs_content import DocsContentError, render_markdown
    from wire3_gtm.evidence import EvidenceSet

    for name in ("01_plan.json", "02_evidence_set.json", "03_analyst_artifact.json", "04_strategy_artifact.json"):
        if not store.exists(name):
            raise DocsContentError(f"cannot write a brief: {name} is missing; finish the text pipeline first")
    plan = ResearchPlan.model_validate_json(store.load_text("01_plan.json"))
    evidence = EvidenceSet.model_validate_json(store.load_text("02_evidence_set.json")).evidence
    analyst = AnalystArtifact.model_validate_json(store.load_text("03_analyst_artifact.json"))
    strategy = StrategyArtifact.model_validate_json(store.load_text("04_strategy_artifact.json"))
    doc = build_executive_brief(strategy, analyst, evidence, plan, store.run_id)
    store.save_text("05_executive_brief.md", render_markdown(doc))
    return str(store.path("05_executive_brief.md"))
