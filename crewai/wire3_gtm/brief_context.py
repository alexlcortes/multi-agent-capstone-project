"""Which brief a run is for, reduced to the values the models and prompts need.

The repo-root brief.json is the Ocala brief (shared with n8n, pinned by the golden
test and the KPIs), so Ocala stays the default. Other briefs live in
briefs/<name>/brief.json and name their table spellings in `competitor_names`.

One run per process, so the active brief is a module global rather than a
contextvar: CrewAI runs some work in threads, which would not see a contextvar.
"""

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT_BRIEF = REPO / "brief.json"  # shared with n8n (scripts/run_pipeline.js)
BRIEFS_DIR = REPO / "briefs"


@dataclass(frozen=True)
class BriefContext:
    key: str  # readable brief name for logs and doc titles
    run_prefix: str  # run ids: <PREFIX>-YYYYMMDD-HHMMSS
    subject: str  # the company the plan is for; always a table row, never required to be priced
    competitors: tuple[str, ...]  # exact table spellings, subject excluded
    region: str
    segment: str  # as an adjective: "the <segment> segment"
    strategy_target: str  # who the Strategy agent plans for
    aliases: dict = field(default_factory=dict)  # brief spelling -> table spelling
    framing_rules: tuple[str, ...] = ()  # brief rules the Strategy agent must follow (it never sees the brief)
    goal_target: str | None = None  # the Strategy agent's goal wording, when it differs from strategy_target
    census_geographies: tuple[str, ...] = ()  # fetched in code from the Census tool after research (none for Ocala)
    census_rq: str | None = None  # the research question the Census evidence answers
    compliance_considerations: tuple[str, ...] = ()  # listed, unresearched, in the equity and compliance section
    customer_segments: tuple[tuple[str, str], ...] = ()  # (name, definition): one ICP each, named after the segment
    segment_rule: str = ""  # how households are assigned to a segment
    archived_pages: tuple[str, ...] = ()  # provider pricing pages read from the Wayback Machine after research
    archived_rq: str | None = None  # the research question the archived pages answer
    company_domains: tuple[str, ...] = ()  # competitor sites classed as "company" sources, beyond the Ocala defaults

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.subject, *self.competitors)


OCALA = BriefContext(
    key="wire3-ocala",
    run_prefix="run",  # unchanged Ocala run ids (run-YYYYMMDD-HHMMSS)
    subject="Wire3",
    competitors=("Spectrum", "AT&T", "T-Mobile Home Internet"),
    region="Ocala, FL / Marion County, FL",
    segment="price/value switcher",
    strategy_target="Wire3's Ocala price/value switcher segment",
    goal_target="Wire3's Ocala price/value-switcher segment",  # the original agents.yaml wording, kept exact
    aliases={"Charter Spectrum": "Spectrum"},
)

_active = OCALA


def active() -> BriefContext:
    return _active


@contextmanager
def using(ctx: BriefContext):
    global _active
    previous, _active = _active, ctx
    try:
        yield ctx
    finally:
        _active = previous


def activate(ctx: BriefContext) -> None:
    """For the CLI: set the brief for the rest of the process."""
    global _active
    _active = ctx


def from_brief(brief: dict) -> BriefContext:
    """A brief without `competitor_names` is the original Ocala brief.json."""
    if "competitor_names" not in brief:
        return OCALA
    names = brief["competitor_names"]
    subject = brief["company"]
    return BriefContext(
        key=brief["brief_key"],
        run_prefix=brief.get("run_prefix") or re.sub(r"[^A-Z0-9]+", "-", brief["brief_key"].upper()),
        subject=subject,
        competitors=tuple(n for n in names if n != subject),
        region=brief["region"],
        segment=brief.get("segment_short") or brief["segment"],
        strategy_target=f"{subject}'s {brief.get('segment_short') or brief['segment']} in {brief['region']}",
        aliases=brief.get("competitor_aliases") or {},
        framing_rules=tuple(brief.get("framing_rules") or ()),
        census_geographies=tuple((brief.get("census") or {}).get("geographies") or ()),
        census_rq=(brief.get("census") or {}).get("research_question_id"),
        compliance_considerations=tuple(brief.get("compliance_considerations") or ()),
        customer_segments=tuple((seg["name"], seg["definition"]) for seg in brief.get("customer_segments") or ()),
        segment_rule=brief.get("segment_rule") or "",
        archived_pages=tuple(p["url"] for p in (brief.get("archived_pages") or {}).get("pages") or ()),
        archived_rq=(brief.get("archived_pages") or {}).get("research_question_id"),
        company_domains=tuple(d.lower() for d in brief.get("company_domains") or ()),
    )


def brief_path(name: str | None) -> Path:
    """None -> the root Ocala brief; a name -> briefs/<name>/brief.json; else a path."""
    if name is None:
        return ROOT_BRIEF
    candidate = BRIEFS_DIR / name / "brief.json"
    path = candidate if candidate.exists() else Path(name)
    if not path.exists():
        found = sorted(p.parent.name for p in BRIEFS_DIR.glob("*/brief.json"))
        raise FileNotFoundError(f"no brief {name!r}; available: {found or 'none'} (or pass a path)")
    return path


def load(name: str | None = None) -> dict:
    return json.loads(brief_path(name).read_text())


def fill(text: str, ctx: BriefContext | None = None) -> str:
    """Fill the [[...]] markers in agents.yaml / tasks.yaml from the active brief."""
    ctx = ctx or active()
    quoted = [f'"{n}"' for n in ctx.all_names]
    values = {
        "subject": ctx.subject,
        "region": ctx.region,
        "segment": ctx.segment,
        "strategy_target": ctx.strategy_target,
        "goal_target": ctx.goal_target or ctx.strategy_target,
        "competitor_names": ", ".join(ctx.all_names),
        "competitor_names_quoted": ", ".join(quoted),
        "competitor_names_quoted_or": f"{', '.join(quoted[:-1])} or {quoted[-1]}",
        "priced_competitors_and": f"{', '.join(ctx.competitors[:-1])} and {ctx.competitors[-1]}",
        "priced_count": str(len(ctx.competitors)),
        "row_count": str(len(ctx.all_names)),
        # leading space: these markers sit right after a sentence and vanish when empty
        "competitor_aliases": "".join(f' Map "{k}" to "{v}".' for k, v in ctx.aliases.items()),
        "archive_note": (
            " Evidence whose source_url starts with https://web.archive.org/web/ is a Wayback Machine snapshot of the "
            "provider's own page, and its publication_date is the snapshot date. Use it for pricing: an 'Everyday "
            "pricing' or regular price shown beside a special offer is the post-promo price; a price shown 'for 1 "
            "year' with no later price leaves post_promo_price null; a price guarantee or lock ('won't change for 5 "
            "years') is a promo_duration_months of that length. Put the snapshot date in plan_name, e.g. "
            "'300 Mbps (Mount Dora page, archived 2026-05-18)'." if ctx.archived_pages else ""),
        "segment_instructions": (
            f" Customer segments from the brief: define exactly one ICP per segment, and start each ICP name with "
            f"its segment name ({', '.join(repr(n) for n, _ in ctx.customer_segments)}). "
            + " ".join(f"{n}: {d}" for n, d in ctx.customer_segments)
            + (f" {ctx.segment_rule}" if ctx.segment_rule else "")
            + " Segments with different barriers need different pains, message pillars and channel mixes; do not "
              "give every ICP the same channels." if ctx.customer_segments else ""),
        "framing_rules": (" Rules from the brief, which override any recommendation that conflicts with them: "
                          + " ".join(f"({i + 1}) {r}" for i, r in enumerate(ctx.framing_rules))
                          if ctx.framing_rules else ""),
    }
    return re.sub(r"\[\[(\w+)\]\]", lambda m: values[m.group(1)], text)


def fill_config(config):
    """fill() every string in a parsed agents.yaml / tasks.yaml."""
    if isinstance(config, dict):
        return {k: fill_config(v) for k, v in config.items()}
    if isinstance(config, list):
        return [fill_config(v) for v in config]
    return fill(config) if isinstance(config, str) else config


def for_run(run_dir: Path) -> BriefContext:
    """The brief a saved run was made for (its 00_brief.json); Ocala when there is none."""
    saved = Path(run_dir) / "00_brief.json"
    return from_brief(json.loads(saved.read_text())) if saved.exists() else OCALA
