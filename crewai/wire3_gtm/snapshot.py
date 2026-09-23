"""Save one validated Strategy output, with everything needed to use it, as a
committed local snapshot, so the Docs Writer and later steps can be built and
tested without a new run or a Google connection.

    snapshots/<run_id>/
      01_plan.json  02_evidence_set.json  03_analyst_artifact.json  04_strategy_artifact.json
      03_analyst_report.json  04_strategy_report.json      (when the run has them)
      strategy.md       the Strategy output, readable, with every cited id resolved
      snapshot.json     source run, checks passed, sha256 of every file above

File names match the run directory's, so the snapshot reads like a finished run.
Nothing is written unless the artifacts pass the same checks as the pipeline's
guardrails plus the shared JSON Schemas; load_snapshot() refuses edited files.
"""

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from wire3_gtm.analyst_models import AnalystArtifact, check_grounding, dump_contract
from wire3_gtm.docs_content import _Builder, _natural, render_markdown, DocumentPlan
from wire3_gtm.evidence import EvidenceSet
from wire3_gtm.models import ResearchPlan
from wire3_gtm.run_store import RunStore
from wire3_gtm.strategy_checks import analyst_ids, grounding_errors, unknown_coverage_errors
from wire3_gtm.strategy_models import StrategyArtifact

REPO = Path(__file__).resolve().parents[2]
SNAPSHOTS_DIR = REPO / "snapshots"
SCHEMAS = REPO / "schemas"

REQUIRED = ("01_plan.json", "02_evidence_set.json", "03_analyst_artifact.json", "04_strategy_artifact.json")
OPTIONAL = ("03_analyst_report.json", "04_strategy_report.json")


class SnapshotError(Exception):
    """The run is incomplete, fails validation, or a snapshot file was edited."""


@dataclass
class Snapshot:
    plan: ResearchPlan
    evidence: EvidenceSet
    analyst: AnalystArtifact
    strategy: StrategyArtifact
    analyst_report: dict | None
    meta: dict


# Schema rules the CrewAI build deliberately departs from, each documented in
# SETUP_DECISIONS.md. Only these exact errors are tolerated; they are listed in
# snapshot.json so a reader of the snapshot sees them.
KNOWN_DEVIATIONS = {
    ("analyst_artifact", "pricing_matrix", "minItems"):
        "pricing_matrix has 3+ rows, not the schema's 4: Wire3's price is not public and is never invented "
        "(SETUP_DECISIONS.md, CrewAI Analyst speed, Decision 2; the Pydantic model enforces the 3-row minimum)",
}


def _schema_errors(name: str, doc, deviations: set[str]) -> list[str]:
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
    errors = []
    for e in jsonschema.Draft202012Validator(schema).iter_errors(doc):
        path = "/".join(map(str, e.absolute_path))
        known = KNOWN_DEVIATIONS.get((name, path, e.validator))
        if known:
            deviations.add(known)
        else:
            errors.append(f"{name}: {path or '(root)'}: {e.message[:200]}")
    return errors


def validate(files: dict[str, str]) -> tuple[Snapshot, list[str]]:
    """Parse and check the artifacts. Returns the typed snapshot and the list of
    checks that ran; raises SnapshotError on the first failing group. Tolerated
    schema deviations are in the snapshot's meta, not silently dropped."""
    try:
        plan = ResearchPlan.model_validate_json(files["01_plan.json"])
        evidence = EvidenceSet.model_validate_json(files["02_evidence_set.json"])
        analyst = AnalystArtifact.model_validate_json(files["03_analyst_artifact.json"])
        strategy = StrategyArtifact.model_validate_json(files["04_strategy_artifact.json"])
    except ValueError as exc:
        raise SnapshotError(f"an artifact fails its Pydantic model: {exc}") from exc

    deviations: set[str] = set()
    errors = _schema_errors("strategy_artifact", json.loads(files["04_strategy_artifact.json"]), deviations)
    errors += _schema_errors("analyst_artifact", json.loads(files["03_analyst_artifact.json"]), deviations)
    for rec in json.loads(files["02_evidence_set.json"])["evidence"]:
        errors += _schema_errors("research_evidence", rec, deviations)
    if errors:
        raise SnapshotError("JSON Schema: " + " | ".join(errors[:10]))

    valid_ids = {e.evidence_id for e in evidence.evidence}
    errors = (check_grounding(analyst, valid_ids) + grounding_errors(strategy, analyst)
              + unknown_coverage_errors(strategy, analyst))
    if strategy.brief_id != analyst.brief_id:
        errors.append(f"strategy brief_id {strategy.brief_id!r} != analyst brief_id {analyst.brief_id!r}")
    if errors:
        raise SnapshotError("grounding: " + " | ".join(errors[:10]))

    checks = [
        "pydantic: plan, evidence set, analyst, strategy",
        "json_schema: strategy_artifact, analyst_artifact, research_evidence (every record)",
        "analyst evidence ids exist in the evidence set",
        "strategy supporting_ids exist in the analyst artifact",
        "every analyst unknown has a follow-up research question",
        "strategy and analyst share one brief_id",
    ]
    reports = {k: json.loads(files[k]) if k in files else None for k in OPTIONAL}
    meta = {"checks_passed": checks, "known_schema_deviations": sorted(deviations)}
    return Snapshot(plan, evidence, analyst, strategy, reports["03_analyst_report.json"], meta), checks


def strategy_markdown(snap: Snapshot, source: str) -> str:
    """The Strategy output on its own, readable, with a closing table that
    resolves every id it cites to the Analyst finding or source behind it."""
    st = dump_contract(snap.strategy)
    b = _Builder()
    b.add(f"Strategy output: {snap.strategy.brief_id}", "TITLE")
    b.add(f"Source run: {source}. Validated snapshot; see snapshot.json for checks and checksums. "
          "Academic strategy exercise, not Wire3's actual go-to-market plan.", "P")
    b.section("Ideal customer profiles", lambda: b.items(st["icps"], "icp_id", "name"))
    b.section("Customer pains and desired outcomes", lambda: b.items(st["pains_and_outcomes"], "pain_id"))
    b.section("Value proposition", lambda: b.obj(st["value_proposition"]))
    b.section("Positioning and differentiation", lambda: b.obj(st["positioning"]))
    b.section("Message pillars", lambda: b.items(st["message_pillars"], "pillar_id", "title"))
    b.section("Recommended channels", lambda: b.items(st["channels"], "channel_id", "channel_name"))
    b.section("Launch phases and activities", lambda: b.items(
        sorted(st["launch_phases"], key=lambda p: p["sequence_order"]), "phase_id", "phase_name"))
    b.section("Success metrics", lambda: b.items(st["success_metrics"], "metric_id", "name"))
    b.section("Risks", lambda: b.items(st["risks"], "risk_id"))
    b.section("Follow-up research questions", lambda: b.items(st["follow_up_research_questions"], "frq_id"))

    an = dump_contract(snap.analyst)
    labels = {t["theme_id"]: t["title"] for t in an["market_themes"]}
    labels |= {i["item_id"]: i["statement"] for group in an["swot"].values() for i in group}
    auc = an["assumptions_unknowns_conflicts"]
    labels |= {a["id"]: a["statement"] for a in auc["assumptions"]}
    labels |= {u["id"]: u["description"] for u in auc["unknowns"]}
    by_ev = {e.evidence_id: e for e in snap.evidence.evidence}
    cited = sorted({i for f in _supporting_ids(st) for i in f}, key=_natural)
    b.add("Cited ids", "H2")
    for i in cited:
        e = by_ev.get(i)
        text = f"{e.source_title or e.source_url} ({e.source_type})" if e else labels.get(i, "?")
        lead = f"[{i}] "
        b.add(lead + text, "BULLET", bold_len=len(lead),
              link={"offset": len(lead), "length": len(e.source_title or e.source_url), "url": e.source_url}
              if e and e.source_url.startswith("http") else None)
    return render_markdown(DocumentPlan("", b.blocks, b.sections, []))


def _supporting_ids(node):
    if isinstance(node, dict):
        if isinstance(node.get("supporting_ids"), list):
            yield node["supporting_ids"]
        for v in node.values():
            yield from _supporting_ids(v)
    elif isinstance(node, list):
        for v in node:
            yield from _supporting_ids(v)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def export_snapshot(store: RunStore, out_dir: Path | None = None, now: datetime | None = None) -> Path:
    missing = [n for n in REQUIRED if not store.exists(n)]
    if missing:
        raise SnapshotError(f"run {store.run_id} has no {', '.join(missing)}; it did not finish the Strategy step")
    files = {n: store.load_text(n) for n in REQUIRED + OPTIONAL if store.exists(n)}
    snap, checks = validate(files)

    out = out_dir or SNAPSHOTS_DIR / store.run_id
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for name, text in files.items():
        (tmp / name).write_text(text)
    files["strategy.md"] = strategy_markdown(snap, store.run_id)
    (tmp / "strategy.md").write_text(files["strategy.md"])
    ids = analyst_ids(snap.analyst)
    meta = {
        "source_run_id": store.run_id,
        "brief_id": snap.strategy.brief_id,
        "exported_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **snap.meta,
        "counts": {
            "evidence_records": len(snap.evidence.evidence),
            "analyst_themes": len(ids["THEME"]), "analyst_unknowns": len(ids["UNK"]),
            **{k: len(getattr(snap.strategy, k)) for k in (
                "icps", "pains_and_outcomes", "message_pillars", "channels", "launch_phases",
                "success_metrics", "risks", "follow_up_research_questions")},
        },
        "sha256": {n: _sha(t) for n, t in sorted(files.items())},
    }
    (tmp / "snapshot.json").write_text(json.dumps(meta, indent=1) + "\n")
    shutil.rmtree(out, ignore_errors=True)  # replace as a whole: never a mix of two exports
    tmp.rename(out)
    return out


def load_snapshot(path: Path | str) -> Snapshot:
    """Read a snapshot back for downstream steps: checksums first (a hand-edited
    file is refused), then the full validation again."""
    path = Path(path)
    meta = json.loads((path / "snapshot.json").read_text())
    files = {n: (path / n).read_text() for n in meta["sha256"] if (path / n).exists()}
    changed = [n for n, h in meta["sha256"].items() if n not in files or _sha(files[n]) != h]
    if changed:
        raise SnapshotError(f"snapshot files changed or missing since export: {changed}; re-export from the run")
    snap, _ = validate(files)
    snap.meta = meta
    return snap
