"""Docs Writer, content layer: validated artifacts -> document blocks.

No LLM and no Google here. The Docs Writer is a renderer, not an agent: it only
accepts content that already passed the pipeline's validation, and turns it
into (a) Markdown you can read locally and (b) Google Docs batchUpdate requests.
Same layout approach as the n8n Docs Writer, plus the Analyst's tables, which
the brief requires (competitor comparison, pricing matrix, evidence summary).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from wire3_gtm.analyst_models import AnalystArtifact, check_grounding, dump_contract
from wire3_gtm.evidence import EvidenceRecord
from wire3_gtm.models import ResearchPlan
from wire3_gtm.strategy_checks import grounding_errors, unknown_coverage_errors
from wire3_gtm.strategy_models import StrategyArtifact

HTTP_URL_RE = re.compile(r"^https?://\S+$", re.I)
EV_RE = re.compile(r"\bEV-[0-9a-f]{8}\b")


class DocsContentError(Exception):
    """The content is incomplete or invalid; nothing is written to Google."""


@dataclass
class Block:
    text: str
    style: str = "P"  # TITLE | H2 | H3 | P | BULLET | BULLET2
    bold_len: int = 0
    link: dict | None = None  # {offset, length, url}
    start: int = 0
    end: int = 0


@dataclass
class DocumentPlan:
    title: str
    blocks: list[Block]
    sections: list[str]
    sources: list[EvidenceRecord]
    expected: dict = field(default_factory=dict)


def u16len(s: str) -> int:
    """Length in UTF-16 code units. Google Docs indexes in these, not in Python
    code points, so an emoji or other astral character counts as 2."""
    return len(s.encode("utf-16-le")) // 2


def _natural(s: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s)]


# --- preflight ---------------------------------------------------------------

def preflight(strategy: StrategyArtifact, analyst: AnalystArtifact, evidence: list[EvidenceRecord]) -> None:
    """Refuse content that is invalid. The document is only ever built from
    artifacts that passed the same hard checks as the guardrails."""
    valid_ids = {e.evidence_id for e in evidence}
    errors = (
        check_grounding(analyst, valid_ids)
        + grounding_errors(strategy, analyst)
        + unknown_coverage_errors(strategy, analyst)
    )
    if not evidence:
        errors.append("evidence set is empty")
    if errors:
        raise DocsContentError("refusing to write a document from invalid content: " + " | ".join(errors[:10]))


# --- block building ----------------------------------------------------------

def _is_cited(v) -> bool:
    return isinstance(v, dict) and "value" in v and "basis" in v and ("supporting_ids" in v or "evidence_ids" in v)


def _is_derived(v) -> bool:
    return isinstance(v, dict) and {"value", "derived_from_evidence_ids", "formula"} <= v.keys()


def _text(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):  # before float/int: bool is an int subclass
        return "Yes" if v else "No"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))  # 10000.0 -> 10000
    if isinstance(v, list):
        return ", ".join(_text(x) for x in v)
    return str(v)


def _cite(f: dict) -> str:
    ids = f.get("supporting_ids") if "supporting_ids" in f else f.get("evidence_ids")
    tag = ", ".join(ids) if ids else ("brief" if f["basis"] == "brief_stated" else f["basis"])
    if ids and f["basis"] == "inference":  # inferred FROM these ids, not stated by them: never show it as sourced
        tag = f"inference; {tag}"
    return f"{_text(f['value'])} [{tag}]"


def _label(k: str) -> str:
    s = k.replace("_", " ")
    return s[:1].upper() + s[1:]


class _Builder:
    def __init__(self):
        self.blocks: list[Block] = []
        self.sections: list[str] = []

    def add(self, text: str, style: str = "P", **extra) -> None:
        self.blocks.append(Block(text, style, **extra))

    def labelled(self, key: str, text: str, style: str = "BULLET") -> None:
        lead = f"{_label(key)}: "
        self.add(lead + text, style, bold_len=len(lead))

    def section(self, name: str, fn) -> None:
        self.sections.append(name)
        self.add(f"{len(self.sections)}. {name}", "H2")
        fn()

    def obj(self, obj: dict, skip=()) -> None:
        for k, v in obj.items():
            if k in skip or v is None:
                continue
            if _is_cited(v):
                self.labelled(k, _cite(v))
            elif _is_derived(v):
                ids = ", ".join(v["derived_from_evidence_ids"])
                self.labelled(k, f"{v['value']} [{ids}] (computed: {v['formula']})")
            elif isinstance(v, list) and v and all(_is_cited(x) for x in v):
                self.add(f"{_label(k)}:", "BULLET", bold_len=len(_label(k)) + 1)
                for c in v:
                    self.add(_cite(c), "BULLET2")
            elif isinstance(v, list) and all(x is None or not isinstance(x, (dict, list)) for x in v):
                self.labelled(k, _text(v))
            elif isinstance(v, list):
                self.add(f"{_label(k)}:", "BULLET", bold_len=len(_label(k)) + 1)
                for item in v:
                    if isinstance(item, dict):
                        self.obj(item)
                    else:
                        self.add(_text(item), "BULLET2")
            elif isinstance(v, dict):
                self.add(f"{_label(k)}:", "BULLET", bold_len=len(_label(k)) + 1)
                self.obj(v)
            else:
                self.labelled(k, _text(v))

    def items(self, items, id_key: str, name_key: str | None = None) -> None:
        for item in items or []:
            name = ""
            if name_key and item.get(name_key) is not None:
                n = item[name_key]
                name = _cite(n) if _is_cited(n) else str(n)
            self.add(f"{item[id_key]}{': ' + name if name else ''}", "H3")
            self.obj(item, [k for k in (id_key, name_key) if k])


def build_document(
    strategy: StrategyArtifact,
    analyst: AnalystArtifact,
    evidence: list[EvidenceRecord],
    plan: ResearchPlan,
    run_id: str,
    analyst_report: dict | None = None,
    now: datetime | None = None,
) -> DocumentPlan:
    preflight(strategy, analyst, evidence)
    st, an = dump_contract(strategy), dump_contract(analyst)
    by_id = {e.evidence_id: e for e in evidence}
    b = _Builder()
    when = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")
    title = f"Wire3 GTM Plan - {run_id} - {when} UTC"

    # findings index: analyst-level ids -> label + the evidence behind them
    findings: dict[str, dict] = {}

    def add_finding(i, label, evs):
        if i:
            findings[i] = {"id": i, "label": str(label or i), "evidence_ids": list(evs or [])}

    for t in an["market_themes"]:
        add_finding(t["theme_id"], t["title"], t["supporting_evidence_ids"])
    for group in an["swot"].values():
        for s in group:
            add_finding(s["item_id"], s["statement"], s["evidence_ids"])
    auc = an["assumptions_unknowns_conflicts"]
    for a in auc["assumptions"]:
        add_finding(a["id"], a["statement"], a["evidence_ids"])
    for u in auc["unknowns"]:
        add_finding(u["id"], u["description"], [])

    cited = set()

    def walk(o):
        if isinstance(o, dict):
            cited.update(o.get("supporting_ids", []) if isinstance(o.get("supporting_ids"), list) else [])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(st)
    cited_findings = sorted((findings[i] for i in cited if i in findings), key=lambda f: _natural(f["id"]))

    b.add("Wire3 Go-To-Market Plan", "TITLE")
    header_at = len(b.blocks)
    b.add("", "P")  # header line, filled in once the source count is known
    b.add(
        "Academic strategy exercise for the capstone: recommendations are illustrative and grounded in public "
        "evidence, not Wire3's actual go-to-market plan. How to read citations: bracketed EV- ids are sources "
        "(Appendix B, with links); THEME-, SWOT-, ASM- and UNK- ids are analyst findings (Appendix A); "
        "[inference] marks judgment with no direct source and [brief] marks a fact given in the project brief.",
        "P",
    )

    def executive_summary():
        vp, pos = st["value_proposition"], st["positioning"]
        b.add(f"Target: {plan.segment} in {plan.region}.", "P")
        b.labelled("value_proposition", _cite(vp["headline"]), "P")
        b.labelled("positioning", _cite(pos["positioning_statement"]), "P")
        b.add(
            f"The plan recommends {len(st['channels'])} channels "
            f"({', '.join(c['channel_name'] for c in st['channels'])}), "
            f"{len(st['launch_phases'])} launch phases and {len(st['success_metrics'])} success metrics, and lists "
            f"{len(st['risks'])} risks and {len(st['follow_up_research_questions'])} follow-up research questions.",
            "P",
        )

    def research_scope():
        dates = sorted(e.retrieval_timestamp[:10] for e in evidence if e.retrieval_timestamp)
        rng = f" retrieved {dates[0]}" + (f" to {dates[-1]}" if dates[-1] != dates[0] else "") if dates else ""
        b.add(
            f"{len(plan.planned_tool_calls)} planned research calls returned {len(evidence)} evidence records"
            f"{rng}. Research questions:",
            "P",
        )
        for q in plan.research_questions:
            lead = f"{q.id}: "
            b.add(lead + q.question, "BULLET", bold_len=len(lead))
        if analyst_report:
            q = analyst_report.get("source_quality") or {}
            if q:
                mix = ", ".join(f"{k}: {v}" for k, v in sorted(q.get("by_source_type", {}).items()))
                b.add(
                    f"Evidence quality: {q['cited_records']} distinct sources are cited in the analysis ({mix}). "
                    f"{q['top_tier_share']:.0%} are primary, company or analyst sources against a target of 80%"
                    f"{' (met)' if q.get('meets_80pct_target') else ' (not met)'}.",
                    "BULLET",
                )
            for label, key in (("Pricing rows adjusted automatically (Wire3 row without a sourced price dropped, or an inferred price removed)", "pricing_repairs"),
                               ("Numbers marked as evidence but not found in the cited text", "unsupported_numbers"),
                               ("Invented evidence ids removed from the analysis", "dropped_ids"),
                               ("Themes whose research-question links were recomputed from their evidence", "theme_rq_repairs")):
                n = len(analyst_report.get(key) or [])
                if n:
                    b.add(f"{label}: {n}.", "BULLET")
            depth = analyst_report.get("rq_source_depth") or {}
            if depth:
                thin = min(depth, key=lambda k: (depth[k], k))
                b.add(f"Research-question coverage: every question with evidence is backed by at least {depth[thin]} "
                      f"distinct cited source(s); the thinnest is {thin}.", "BULLET")
            for gap in analyst_report.get("coverage_gaps") or []:
                b.add(f"Coverage gap accepted because the time budget was reached: {gap}", "BULLET")
            if analyst_report.get("salvaged_from"):
                b.add(f"The analysis was recovered from a saved draft ({analyst_report['salvaged_from']}).", "BULLET")

    b.section("Executive summary", executive_summary)
    b.section("Research scope and evidence", research_scope)
    b.section("Competitor comparison", lambda: [
        (b.add(r["competitor_name"], "H3"), b.obj(r, ["competitor_name"])) for r in an["competitor_comparison_table"]])
    b.section("Product and feature comparison", lambda: [
        (b.add(r["competitor_name"], "H3"), b.obj(r, ["competitor_name"])) for r in an["product_feature_comparison"]])
    b.section("Pricing matrix", lambda: [
        (b.add(f"{r['competitor_name']}: {r['plan_name']['value']}", "H3"),
         b.obj(r, ["competitor_name", "plan_name"])) for r in an["pricing_matrix"]])

    def themes():
        for t in an["market_themes"]:
            b.add(f"{t['theme_id']}: {t['title']}", "H3")
            b.obj({"description": t["description"],
                   "related_research_questions": t["related_research_question_ids"],
                   "competitors_involved": t.get("competitors_involved") or [],
                   "evidence": t["supporting_evidence_ids"]})

    b.section("Market themes", themes)

    def swot():
        for group, items in an["swot"].items():
            b.add(_label(group), "H3")
            for s in items:
                tag = ", ".join(s["evidence_ids"]) if s["evidence_ids"] else ("brief" if s["basis"] == "brief_stated" else s["basis"])
                lead = f"{s['item_id']}: "
                b.add(f"{lead}{s['statement']} [{tag}]", "BULLET", bold_len=len(lead))

    b.section("SWOT analysis", swot)
    b.section("7P market analysis", lambda: [
        (b.add(_label(k), "H3"), b.obj(v)) for k, v in an["seven_p_analysis"].items()])
    b.section("Ideal customer profiles", lambda: b.items(st["icps"], "icp_id", "name"))
    b.section("Customer pains and desired outcomes", lambda: b.items(st["pains_and_outcomes"], "pain_id"))
    b.section("Value proposition", lambda: b.obj(st["value_proposition"]))
    b.section("Positioning and differentiation", lambda: b.obj(st["positioning"]))
    b.section("Message pillars", lambda: b.items(st["message_pillars"], "pillar_id", "title"))
    b.section("Recommended channels", lambda: b.items(st["channels"], "channel_id", "channel_name"))
    b.section("Launch phases and activities", lambda: b.items(
        sorted(st["launch_phases"], key=lambda p: p.get("sequence_order", 0)), "phase_id", "phase_name"))
    b.section("Success metrics", lambda: b.items(st["success_metrics"], "metric_id", "name"))
    b.section("Risks", lambda: b.items(st["risks"], "risk_id"))

    def auc_section():
        for label, key in (("Assumptions", "assumptions"), ("Unknowns", "unknowns"), ("Conflicts", "conflicts")):
            b.add(label, "H3")
            if not auc[key]:
                b.add("None recorded.", "P")
            for x in auc[key]:
                b.add(x["id"], "P", bold_len=len(x["id"]))
                b.obj({k: v for k, v in x.items() if k != "id"})

    b.section("Assumptions, unknowns and conflicts", auc_section)
    b.section("Follow-up research questions", lambda: b.items(st["follow_up_research_questions"], "frq_id"))

    b.add("Appendix A: Analyst findings referenced", "H2")
    for f in cited_findings:
        lead = f"[{f['id']}] "
        evs = " ".join(f"[{e}]" for e in f["evidence_ids"]) or "no direct source evidence (assumption or open unknown)"
        b.add(f"{lead}{f['label']} -- {evs}", "BULLET", bold_len=len(lead))

    # every EV id that appears anywhere so far must be listed in Appendix B
    source_ids = sorted({m for blk in b.blocks for m in EV_RE.findall(blk.text)}, key=_natural)
    orphaned = [i for i in source_ids if i not in by_id]
    if orphaned:
        raise DocsContentError(f"orphaned evidence id(s) in the document: {orphaned}")
    sources = [by_id[i] for i in source_ids]

    b.add("Appendix B: Sources", "H2")
    for e in sources:
        lead = f"[{e.evidence_id}] "
        name = e.source_title or e.source_url
        ok = bool(HTTP_URL_RE.match(e.source_url or ""))
        tail = f" ({e.source_type}{', ' + e.publication_date if e.publication_date else ''})"
        b.add(lead + name + tail, "BULLET", bold_len=len(lead),
              link={"offset": len(lead), "length": len(name), "url": e.source_url} if ok else None)

    b.blocks[header_at].text = (
        f"Run: {run_id} | Brief: {strategy.brief_id} | Generated: {when} UTC | Sources cited: {len(sources)}"
    )
    # table sections: each row is an H3 under the section's H2 (tables render as
    # labelled lists for now); the verifier checks every row survived the write
    table_sections = {"Competitor comparison", "Product and feature comparison", "Pricing matrix",
                      "SWOT analysis", "7P market analysis"}
    tables, current = {}, None
    for blk in b.blocks:
        if blk.style == "H2":
            name = blk.text.split(". ", 1)[-1]
            current = blk.text if name in table_sections else None
            if current:
                tables[current] = []
        elif blk.style == "H3" and current:
            tables[current].append(blk.text)
    expected = {
        "title": title,
        "tables": tables,
        "section_headings": [f"{i + 1}. {s}" for i, s in enumerate(b.sections)],
        "source_ids": [e.evidence_id for e in sources],
        "link_urls": [e.source_url for e in sources if HTTP_URL_RE.match(e.source_url or "")],
        "allowed_evidence_ids": sorted(by_id),
    }
    return DocumentPlan(title, b.blocks, b.sections, sources, expected)


# --- renderers ---------------------------------------------------------------

def render_markdown(plan: DocumentPlan) -> str:
    out = []
    for blk in plan.blocks:
        text = blk.text
        if blk.link:  # offsets refer to the original text; the bold lead-in sits before the link
            o, n = blk.link["offset"], blk.link["length"]
            text = f"{text[:o]}[{text[o:o + n]}]({blk.link['url']}){text[o + n:]}"
        if blk.bold_len:
            text = f"**{blk.text[:blk.bold_len].rstrip()}** " + text[blk.bold_len:]
        prefix = {"TITLE": "# ", "H2": "\n## ", "H3": "\n### ", "P": "", "BULLET": "- ", "BULLET2": "  - "}[blk.style]
        out.append(prefix + text)
    return "\n".join(out) + "\n"


def to_docs_requests(plan: DocumentPlan) -> list[dict]:
    """Docs API batchUpdate requests: one insertText, then paragraph styles,
    bold lead-ins and links (text-only styles, so no index shifting), and
    bullets last. Indices are UTF-16 code units, starting at 1."""
    pos, full = 1, []
    for blk in plan.blocks:
        blk.start = pos
        full.append(blk.text + "\n")
        pos += u16len(blk.text) + 1
        blk.end = pos
    requests: list[dict] = [{"insertText": {"location": {"index": 1}, "text": "".join(full)}}]
    style_map = {"TITLE": "TITLE", "H2": "HEADING_2", "H3": "HEADING_3", "P": "NORMAL_TEXT",
                 "BULLET": "NORMAL_TEXT", "BULLET2": "NORMAL_TEXT"}
    for blk in plan.blocks:
        requests.append({"updateParagraphStyle": {
            "range": {"startIndex": blk.start, "endIndex": blk.end},
            "paragraphStyle": {"namedStyleType": style_map[blk.style]}, "fields": "namedStyleType"}})
    for blk in plan.blocks:
        if blk.bold_len:
            requests.append({"updateTextStyle": {
                "range": {"startIndex": blk.start, "endIndex": blk.start + u16len(blk.text[:blk.bold_len])},
                "textStyle": {"bold": True}, "fields": "bold"}})
        if blk.link:
            o = u16len(blk.text[:blk.link["offset"]])
            n = u16len(blk.text[blk.link["offset"]:blk.link["offset"] + blk.link["length"]])
            requests.append({"updateTextStyle": {
                "range": {"startIndex": blk.start + o, "endIndex": blk.start + o + n},
                "textStyle": {"link": {"url": blk.link["url"]}}, "fields": "link"}})
    run = None
    for blk in plan.blocks + [Block("", "P")]:  # sentinel flushes the last run
        if blk.style in ("BULLET", "BULLET2"):
            run = (run[0], blk.end) if run else (blk.start, blk.end)
        elif run:
            requests.append({"createParagraphBullets": {
                "range": {"startIndex": run[0], "endIndex": run[1]}, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}})
            run = None
    return requests
