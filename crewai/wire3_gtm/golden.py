"""Scenario checks for a finished run against the golden brief.

"Golden" here is NOT a word-for-word expected document. The LLM steps are not deterministic, so two
good runs of the same brief never share wording, and a text diff would fail on every run while
catching nothing that matters. Golden means three fixed things:

  1. a pinned INPUT: brief.json, by sha256 (GOLDEN_BRIEF_SHA256). If the brief changes, every
     expectation below may be wrong, so the tests stop and ask for a re-baseline instead of passing;
  2. a pinned REFERENCE RUN: the committed snapshot (checksummed by snapshot.py), which the checks
     must pass. It proves the checks are satisfiable, and gives the counts the bands are set from;
  3. INVARIANTS and BANDS that any acceptable run of this brief must meet: every required section,
     every research question sourced, every id resolvable, every uncertain claim marked, length within
     a range. These are properties of the brief, not of one run's text.

Each check reads plain dicts and returns a list of problems (empty = pass). They deliberately do not
reuse the pipeline's own guardrails, so a bug in a guardrail cannot make its own test pass.
"""

import re
from typing import Iterable

GOLDEN_BRIEF_SHA256 = "308106b4f81ff1b10e972b6e6630e8d1ce9a61071f07528819bbc5ef9e3f8f52"
GOLDEN_SNAPSHOT = "run-20260921-121832"

# The brief's required_output_sections, in its order, -> the document heading(s) that satisfy each.
# A brief section this map does not know fails the check: renaming a section must be a deliberate edit.
SECTION_MAP = {
    "Executive summary": ["Executive summary"],
    "Research scope & evidence table": ["Research scope and evidence"],
    "Competitor comparison table": ["Competitor comparison"],
    "Pricing matrix": ["Pricing matrix"],
    "Ideal customer profile(s)": ["Ideal customer profiles"],
    "Customer pains and desired outcomes": ["Customer pains and desired outcomes"],
    "Value proposition and positioning": ["Value proposition", "Positioning and differentiation"],
    "Message pillars": ["Message pillars"],
    "Recommended channels": ["Recommended channels"],
    "Launch phases and activities": ["Launch phases and activities"],
    "Success metrics": ["Success metrics"],
    "Risks, assumptions, and open follow-up research questions":
        ["Risks", "Assumptions, unknowns and conflicts", "Follow-up research questions"],
    "Citations / evidence appendix": ["Appendix B: Sources"],
}

# Strategy sections whose content must trace back to at least one source (EV-), directly or through
# an analyst finding. follow_up_research_questions is excluded: it exists for what is NOT known.
TRACED_STRATEGY_SECTIONS = ("icps", "pains_and_outcomes", "value_proposition", "positioning", "message_pillars",
                            "channels", "launch_phases", "success_metrics", "risks")

# Length band, in Markdown words. The golden document is 5,183 words over 61 sources. The band is wide
# on purpose (runs legitimately cite more or fewer sources) and exists to catch a truncated document
# (a step that returned almost nothing) or a runaway one (duplicated sections, dumped excerpts).
MIN_WORDS, MAX_WORDS = 2_500, 10_000
MIN_SOURCES, MAX_SOURCES = 20, 150
MAX_SECTION_SHARE = 0.40  # no one numbered section may be most of the document

TITLE_RE = re.compile(r"^Wire3 GTM Plan - (?!None\b)[A-Za-z0-9][A-Za-z0-9-]* - \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$")
EV_RE = re.compile(r"\bEV-[0-9a-f]{8}\b")
URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.I)
# Python values leaking into prose: None, nan, dict/list reprs, empty citation brackets.
LEAK_RE = re.compile(r"\bNone\b|\bnan\b|\{'|\['|\[\]")


def _walk(node) -> Iterable[dict]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _ev_ids(node) -> set[str]:
    return {m for d in _walk(node) for k in ("evidence_ids", "supporting_evidence_ids", "derived_from_evidence_ids")
            for m in (d.get(k) or []) if isinstance(m, str)}


def _headings(blocks) -> list[str]:
    """H2 texts without their "N. " numbering."""
    return [re.sub(r"^\d+\. ", "", b.text) for b in blocks if b.style == "H2"]


def _finding_evidence(analyst: dict) -> dict[str, set[str]]:
    """Analyst finding id -> the EV ids behind it. Unknowns have none by definition."""
    out = {t["theme_id"]: set(t["supporting_evidence_ids"]) for t in analyst["market_themes"]}
    for group in analyst["swot"].values():
        out.update({s["item_id"]: set(s["evidence_ids"]) for s in group})
    auc = analyst["assumptions_unknowns_conflicts"]
    out.update({a["id"]: set(a["evidence_ids"]) for a in auc["assumptions"]})
    out.update({u["id"]: set() for u in auc["unknowns"]})
    return out


# --- 1. required sections ------------------------------------------------------------------------

def section_errors(brief: dict, blocks) -> list[str]:
    errors, headings = [], _headings(blocks)
    last = -1
    for required in brief["required_output_sections"]:
        if required not in SECTION_MAP:
            errors.append(f"brief section {required!r} has no mapping in golden.SECTION_MAP")
            continue
        for heading in SECTION_MAP[required]:
            if heading not in headings:
                errors.append(f"missing section {heading!r} (brief: {required!r})")
                continue
            at = headings.index(heading)
            if at < last:
                errors.append(f"section {heading!r} is out of the brief's order")
            last = max(last, at)
    if len(headings) != len(set(headings)):
        errors.append("a section heading appears twice")
    numbered = [b.text for b in blocks if b.style == "H2" and re.match(r"^\d+\. ", b.text)]
    if [int(t.split(".")[0]) for t in numbered] != list(range(1, len(numbered) + 1)):
        errors.append("numbered sections are not numbered 1..N in order")
    # a heading with nothing under it satisfies a heading search but not the brief
    for i, b in enumerate(blocks):
        if b.style == "H2":
            nxt = next((x for x in blocks[i + 1:] if x.style == "H2"), None)
            body = blocks[i + 1: blocks.index(nxt) if nxt else len(blocks)]
            if not any(x.text.strip() for x in body):
                errors.append(f"section {b.text!r} is empty")
    return errors


# --- 2. research-question coverage -----------------------------------------------------------------

def rq_coverage_errors(brief: dict, plan: dict, evidence: list[dict], analyst: dict, markdown: str) -> list[str]:
    errors = []
    rqs = [q["id"] for q in plan["research_questions"]]
    if len(rqs) != len(brief["research_questions"]):
        errors.append(f"plan has {len(rqs)} research questions, the brief {len(brief['research_questions'])}")
    # One search result is stored once per research question it served, each copy with its own id, so a
    # question counts as sourced when any copy of any result returned for it is cited.
    result = lambda e: (e["source_url"], e["excerpt"])  # noqa: E731
    rqs_of: dict[tuple, set[str]] = {}
    for e in evidence:
        rqs_of.setdefault(result(e), set()).add(e["research_question_id"])
    by_id = {e["evidence_id"]: e for e in evidence}
    collected = {e["research_question_id"] for e in evidence}
    cited = {rq for i in _ev_ids(analyst) if i in by_id for rq in rqs_of[result(by_id[i])]}
    for rq in rqs:
        if rq not in collected:
            errors.append(f"{rq} has no evidence records")
        elif rq not in cited:
            errors.append(f"{rq} has evidence but no source for it is cited in the analysis")
        if not re.search(rf"\b{rq}:", markdown):
            errors.append(f"{rq} is not listed in the document's research scope")
    return errors


# --- 3. evidence ids flow through analysis and strategy ---------------------------------------------

def evidence_trace_errors(evidence: list[dict], analyst: dict, strategy: dict, markdown: str) -> list[str]:
    errors = []
    known = {e["evidence_id"] for e in evidence}
    analyst_evs = _ev_ids(analyst)
    if not analyst_evs:
        errors.append("the analysis cites no evidence ids")
    if invented := sorted(analyst_evs - known):
        errors.append(f"analysis cites evidence ids not in the evidence set: {invented[:5]}")

    findings = _finding_evidence(analyst)
    refs = lambda node: {i for d in _walk(node) for i in (d.get("supporting_ids") or [])}  # noqa: E731
    all_refs = refs(strategy)
    if unknown := sorted(i for i in all_refs if not (i in findings or i in known)):
        errors.append(f"strategy cites ids that are neither analyst findings nor evidence: {unknown[:5]}")
    for section in TRACED_STRATEGY_SECTIONS:
        traced = {ev for i in refs(strategy[section]) for ev in (findings.get(i, set()) | ({i} & known))}
        if not traced:
            errors.append(f"strategy section {section!r} traces to no source evidence")

    doc_evs = set(EV_RE.findall(markdown))
    if missing := sorted(analyst_evs - doc_evs):
        errors.append(f"evidence cited in the analysis is missing from the document: {missing[:5]}")
    appendix = markdown.split("## Appendix B: Sources", 1)[-1]
    listed, body = set(EV_RE.findall(appendix)), set(EV_RE.findall(markdown.split("## Appendix B: Sources", 1)[0]))
    if body != listed:
        errors.append(f"Appendix B does not list exactly the cited sources "
                      f"(unlisted: {sorted(body - listed)[:3]}, extra: {sorted(listed - body)[:3]})")
    return errors


# --- 4. competitor and pricing rows have valid links -------------------------------------------------

def row_link_errors(analyst: dict, evidence: list[dict], markdown: str, link_check: dict | None) -> list[str]:
    errors = []
    by_id = {e["evidence_id"]: e for e in evidence}
    broken = {b["url"] for b in (link_check or {}).get("broken", [])} | set((link_check or {}).get("malformed", []))
    for table in ("competitor_comparison_table", "pricing_matrix"):
        for row in analyst[table]:
            name = f"{table}/{row['competitor_name']}"
            urls = [by_id[i]["source_url"] for i in _ev_ids(row) if i in by_id]
            if not urls:
                errors.append(f"{name} cites no source")
            for u in urls:
                if not URL_RE.match(u or ""):
                    errors.append(f"{name} cites a malformed URL: {u!r}")
                elif u in broken:
                    errors.append(f"{name} cites a broken link: {u}")
                elif f"]({u})" not in markdown:
                    errors.append(f"{name} source is not a hyperlink in the document: {u}")
    competitors = {r["competitor_name"] for r in analyst["competitor_comparison_table"]}
    priced = {r["competitor_name"] for r in analyst["pricing_matrix"]}
    if missing := sorted({"Wire3", "Spectrum", "AT&T", "T-Mobile Home Internet"} - competitors):
        errors.append(f"competitor comparison is missing {missing}")
    if missing := sorted({"Spectrum", "AT&T", "T-Mobile Home Internet"} - priced):
        errors.append(f"pricing matrix has no row for {missing}")
    if link_check is None:
        errors.append("no link check for this run")
    elif link_check.get("status") == "unchecked":
        errors.append(f"links were not checked: {link_check.get('error')}")
    return errors


# --- 5. uncertain claims are flagged ---------------------------------------------------------------

def uncertainty_errors(analyst: dict, strategy: dict, markdown: str, report: dict | None) -> list[str]:
    errors = []
    if "[inference] marks" not in markdown:
        errors.append("the document does not explain the [inference] marker")
    unknowns = [u["id"] for u in analyst["assumptions_unknowns_conflicts"]["unknowns"]]
    followed = {i for f in strategy["follow_up_research_questions"] for i in f["related_unknown_ids"]}
    for u in unknowns:
        if u not in followed:
            errors.append(f"unknown {u} has no follow-up research question")
        if u not in markdown:
            errors.append(f"unknown {u} is not in the document")
    flagged = len((report or {}).get("unsupported_numbers") or [])
    if flagged and f"Numbers marked as evidence but not found in the cited text: {flagged}." not in markdown:
        errors.append(f"{flagged} unsupported number(s) were flagged by the analysis but not surfaced in the document")
    return errors


def inference_marking_errors(analyst: dict, strategy: dict, render) -> list[str]:
    """Every field whose basis is 'inference' reads as inference in the document, even when it lists
    the ids it was inferred from. `render` is the Docs Writer's cited-field renderer."""
    errors = []
    for where, doc in (("analysis", analyst), ("strategy", strategy)):
        for d in _walk(doc):
            if d.get("basis") == "inference" and "value" in d and "inference" not in render(d).rsplit("[", 1)[-1]:
                errors.append(f"{where}: inferred value rendered as if sourced: {render(d)[:80]!r}")
    return errors


# --- 6. length and format --------------------------------------------------------------------------

def format_errors(doc_plan, markdown: str) -> list[str]:
    errors = []
    if not TITLE_RE.match(doc_plan.title):
        errors.append(f"title does not match the expected format: {doc_plan.title!r}")
    words = len(markdown.split())
    if not MIN_WORDS <= words <= MAX_WORDS:
        errors.append(f"document is {words} words, expected {MIN_WORDS}-{MAX_WORDS}")
    if not MIN_SOURCES <= len(doc_plan.sources) <= MAX_SOURCES:
        errors.append(f"document cites {len(doc_plan.sources)} sources, expected {MIN_SOURCES}-{MAX_SOURCES}")
    sections = re.split(r"\n## \d+\. ", markdown)[1:]
    for s in sections:
        share = len(s.split()) / max(words, 1)
        if share > MAX_SECTION_SHARE:
            errors.append(f"section {s.splitlines()[0]!r} is {share:.0%} of the document")
    for line in markdown.splitlines():
        if LEAK_RE.search(line):
            errors.append(f"raw value leaked into the text: {line[:80]!r}")
            break
    appendix = markdown.split("## Appendix B: Sources", 1)[-1]
    if bad := [ln for ln in appendix.splitlines() if ln.startswith("- ") and "](http" not in ln]:
        errors.append(f"{len(bad)} Appendix B source(s) without a hyperlink, e.g. {bad[0][:80]!r}")
    return errors
