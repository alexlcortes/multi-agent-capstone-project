"""Prompt/model A/B experiments: run variants the same way, record quality, latency and cost per run,
and decide with a rule written down BEFORE the runs (in the experiment file), not by feel.

    uv run python -m wire3_gtm ab run     ../eval/ab/<experiment>.yaml   # N runs per variant, interleaved
    uv run python -m wire3_gtm ab packets ../eval/ab/<experiment>.yaml   # blinded documents for rubric review
    uv run python -m wire3_gtm ab report  ../eval/ab/<experiment>.yaml   # table + the pre-registered decision

Experiment file:

    name: analyst-sources
    variants: {A: variants/control.yaml, B: variants/strict-sources.yaml}   # paths relative to this file
    repeats: 3                      # runs per variant; alternated A,B then B,A so drift hits both equally
    seed_snapshot: run-20260921-121832   # reuse this snapshot's plan + evidence: only Analyst and Strategy
                                         # run, so the variants see identical evidence. "fresh" = full pipeline
    decision:
      min_successful_runs: 3        # per variant, or the report says "no decision"
      primary: rubric_total         # rubric_total (blinded human review) or golden_errors (automated)
      min_gain: 2                   # B's median primary must beat A's by at least this much
      max_cost_increase_pct: 25     # guardrails: B may not cost / take / fail more than this
      max_latency_increase_pct: 25
      max_golden_error_increase: 0

Everything is written under <experiment dir>/<name>/: results.jsonl (one row per run), packets/ (blinded
documents and the unblinding key), reviews/ (rubric scores keyed by packet label).
"""

import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from wire3_gtm import golden, variant
from wire3_gtm.run_store import RUNS_DIR
from wire3_gtm.snapshot import SNAPSHOTS_DIR

CRITERIA = ("clarity", "feasibility", "differentiation", "evidence_quality", "citation_completeness", "usefulness")
REPO = Path(__file__).resolve().parents[2]
BRIEF = REPO / "brief.json"
LOG = Path(__file__).parent.parent / "logs" / "runs.jsonl"
SEEDED = ("01_plan.json", "02_evidence_set.json")


# --- experiment file ---------------------------------------------------------------------------

def load_experiment(path: str | Path) -> dict:
    path = Path(path).resolve()
    exp = yaml.safe_load(path.read_text())
    for key in ("name", "variants", "repeats", "decision"):
        if key not in exp:
            raise ValueError(f"{path}: '{key}' is required")
    if set(exp["variants"]) != {"A", "B"}:
        raise ValueError(f"{path}: variants must be exactly A (control) and B (challenger)")
    exp["variants"] = {k: str((path.parent / v).resolve()) for k, v in exp["variants"].items()}
    for p in exp["variants"].values():
        variant.load(p)  # fail before spending anything
    d = exp["decision"]
    if d.get("primary") not in ("rubric_total", "golden_errors"):
        raise ValueError(f"{path}: decision.primary must be rubric_total or golden_errors")
    exp.setdefault("seed_snapshot", golden.GOLDEN_SNAPSHOT)
    exp["dir"] = path.parent / exp["name"]
    return exp


def schedule(repeats: int) -> list[tuple[int, str]]:
    """A,B, B,A, A,B, ...: each variant goes first equally often, so time-of-day or provider drift
    within the experiment does not systematically favour one of them."""
    out = []
    for i in range(repeats):
        out += [(i, v) for v in (("A", "B") if i % 2 == 0 else ("B", "A"))]
    return out


# --- running -----------------------------------------------------------------------------------

def seed_run(run_dir: Path, snapshot: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)  # never reuse a run directory: that would resume, not run
    for name in SEEDED:
        shutil.copy(SNAPSHOTS_DIR / snapshot / name, run_dir / name)


def run_one(exp: dict, repeat: int, label: str, runner=subprocess.run, now=None) -> dict:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    run_id = f"ab-{exp['name']}-{label}{repeat}-{stamp}"
    if exp["seed_snapshot"] != "fresh":
        seed_run(RUNS_DIR / run_id, exp["seed_snapshot"])
    env = {**os.environ, variant.ENV: exp["variants"][label]}
    t0 = time.time()
    proc = runner([sys.executable, "-m", "wire3_gtm", "run", run_id, "--docs", "local"],
                  cwd=Path(__file__).parent.parent, env=env, capture_output=True, text=True)
    row = {"experiment": exp["name"], "variant": label, "variant_name": variant.load(exp["variants"][label])["name"],
           "repeat": repeat, "run_id": run_id, "exit_code": proc.returncode,
           "wall_clock_ms": int((time.time() - t0) * 1000), "seed_snapshot": exp["seed_snapshot"]}
    row.update(collect(run_id))
    if proc.returncode != 0:
        row["stderr_tail"] = (proc.stderr or "")[-800:]
    return row


def run_record(run_id: str, log: Path | None = None) -> dict | None:
    log = log or LOG
    if not log.exists():
        return None
    recs = [json.loads(l) for l in log.read_text().splitlines() if f'"{run_id}"' in l]
    return next((r for r in reversed(recs) if r.get("event_type") == "run_record" and r.get("run_id") == run_id), None)


def collect(run_id: str, runs_dir: Path | None = None, log: Path | None = None) -> dict:
    """Latency and cost from the run_record; quality from the golden checks on the run's own artifacts."""
    runs_dir = runs_dir or RUNS_DIR
    rec = run_record(run_id, log) or {}
    agents = {a["agent"]: a for a in rec.get("agents") or []}
    row = {
        "status": rec.get("status", "no_run_record"),
        "latency_ms": rec.get("duration_ms"),
        "latency_ms_by_agent": {k: a.get("duration_ms") for k, a in agents.items() if a.get("llm_calls")},
        "cost_usd": (rec.get("cost") or {}).get("estimated_llm_usd"),
        "tokens": rec.get("tokens"),
        "model": rec.get("model"),
        "llm_calls_by_agent": {k: a.get("llm_calls") for k, a in agents.items() if a.get("llm_calls")},
    }
    row.update(quality(runs_dir / run_id))
    return row


def quality(run_dir: Path) -> dict:
    """Automated quality: the golden scenario checks (error count per check) and the Analyst's own
    measurements. Only computable when the run produced both artifacts."""
    from wire3_gtm.analyst_models import AnalystArtifact, dump_contract
    from wire3_gtm.docs_content import _cite, build_document, render_markdown
    from wire3_gtm.evidence import EvidenceSet
    from wire3_gtm.models import ResearchPlan
    from wire3_gtm.strategy_models import StrategyArtifact

    need = ("01_plan.json", "02_evidence_set.json", "03_analyst_artifact.json", "04_strategy_artifact.json")
    if not all((run_dir / n).exists() for n in need):
        return {"golden_errors": None, "golden_by_check": None}
    read = lambda n: (run_dir / n).read_text()  # noqa: E731
    plan = ResearchPlan.model_validate_json(read(need[0]))
    evidence = EvidenceSet.model_validate_json(read(need[1])).evidence
    analyst = AnalystArtifact.model_validate_json(read(need[2]))
    strategy = StrategyArtifact.model_validate_json(read(need[3]))
    report = json.loads(read("03_analyst_report.json")) if (run_dir / "03_analyst_report.json").exists() else None
    links = json.loads(read("06_link_check.json")) if (run_dir / "06_link_check.json").exists() else None
    from wire3_gtm.docs_content import DocsContentError

    try:
        doc = build_document(strategy, analyst, evidence, plan, run_dir.name, analyst_report=report,
                             now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    except DocsContentError as exc:  # the run's artifacts cannot make a document: a failed run, not a crash
        return {"golden_errors": None, "golden_by_check": None, "quality_error": str(exc)[:300]}
    md = render_markdown(doc)
    ev = [e.model_dump() for e in evidence]
    a, s = dump_contract(analyst), dump_contract(strategy)
    brief = json.loads(BRIEF.read_text())
    checks = {
        "sections": golden.section_errors(brief, doc.blocks),
        "rq": golden.rq_coverage_errors(brief, plan.model_dump(), ev, a, md),
        "trace": golden.evidence_trace_errors(ev, a, s, md),
        "links": golden.row_link_errors(a, ev, md, links),
        "uncertainty": golden.uncertainty_errors(a, s, md, report),
        "inference": golden.inference_marking_errors(a, s, _cite),
        "format": golden.format_errors(doc, md),
    }
    q = (report or {}).get("source_quality") or {}
    return {
        "golden_errors": sum(len(v) for v in checks.values()),
        "golden_by_check": {k: len(v) for k, v in checks.items()},
        "words": len(md.split()),
        "sources_cited": len(doc.sources),
        "top_tier_share": q.get("top_tier_share"),
        "unsupported_numbers": len((report or {}).get("unsupported_numbers") or []),
        "dropped_ids": len((report or {}).get("dropped_ids") or []),
    }


def run_experiment(exp: dict, runner=subprocess.run, log=print) -> Path:
    out = exp["dir"] / "results.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    plan = schedule(exp["repeats"])
    log(f"{exp['name']}: {len(plan)} runs ({exp['repeats']} per variant), seed={exp['seed_snapshot']}")
    for n, (repeat, label) in enumerate(plan, 1):
        row = run_one(exp, repeat, label, runner)
        with out.open("a") as f:
            f.write(json.dumps(row) + "\n")
        log(f"[{n}/{len(plan)}] {label}{repeat} {row['run_id']}: {row['status']}, "
            f"{(row['latency_ms'] or 0) / 60000:.1f} min, ${row['cost_usd'] or 0:.3f}, golden errors {row['golden_errors']}")
    return out


# --- blinded review packets --------------------------------------------------------------------

def read_results(exp: dict) -> list[dict]:
    p = exp["dir"] / "results.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def make_packets(exp: dict, runs_dir: Path | None = None, seed: int | None = None) -> Path:
    """Copy every finished run's document to packets/Doc-NN.md in shuffled order, with the key in
    packets/key.json. Reviewers read only Doc-NN files; the report unblinds via the key."""
    runs_dir = runs_dir or RUNS_DIR
    rows = [r for r in read_results(exp) if (runs_dir / r["run_id"] / "05_document.md").exists()]
    if not rows:
        raise ValueError("no finished runs with a document yet: run the experiment first")
    pdir = exp["dir"] / "packets"
    if (pdir / "key.json").exists():
        raise FileExistsError(f"{pdir}/key.json exists: packets are made once, so reviews stay matched to the key")
    pdir.mkdir(parents=True)
    seed = seed if seed is not None else random.SystemRandom().randrange(2**32)
    order = rows[:]
    random.Random(seed).shuffle(order)
    key = {}
    for i, r in enumerate(order, 1):
        label = f"Doc-{i:02d}"
        text = (runs_dir / r["run_id"] / "05_document.md").read_text()
        # the header line names the run id, which would unblind the reviewer
        text = text.replace(r["run_id"], label)
        (pdir / f"{label}.md").write_text(text)
        key[label] = {"run_id": r["run_id"], "variant": r["variant"]}
    (pdir / "key.json").write_text(json.dumps({"seed": seed, "key": key}, indent=1))
    template = {"reviewer": "", "reviewed_at": "", "rubric": "eval/rubric.md", "blind": True,
                "scores": {label: {c: {"score": None, "evidence": ""} for c in CRITERIA} for label in key}}
    (exp["dir"] / "reviews").mkdir(exist_ok=True)
    (exp["dir"] / "reviews" / "TEMPLATE.json").write_text(json.dumps(template, indent=1))
    return pdir


def rubric_totals(exp: dict) -> dict[str, list[int]]:
    """run_id -> rubric totals, one per complete review. A review missing any criterion is not used."""
    key_file = exp["dir"] / "packets" / "key.json"
    if not key_file.exists():
        return {}
    key = json.loads(key_file.read_text())["key"]
    out: dict[str, list[int]] = {}
    for f in sorted((exp["dir"] / "reviews").glob("*.json")):
        if f.name == "TEMPLATE.json":
            continue
        for label, scores in json.loads(f.read_text()).get("scores", {}).items():
            vals = [(scores.get(c) or {}).get("score") for c in CRITERIA]
            if label in key and all(isinstance(v, int) and 1 <= v <= 5 for v in vals):
                out.setdefault(key[label]["run_id"], []).append(sum(vals))
    return out


# --- report and decision -----------------------------------------------------------------------

def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def summarize(rows: list[dict], rubric: dict[str, list[int]]) -> dict[str, dict]:
    out = {}
    for label in ("A", "B"):
        mine = [r for r in rows if r["variant"] == label]
        ok = [r for r in mine if r["status"] in ("success", "degraded") and r.get("golden_errors") is not None]
        per_run_rubric = [statistics.mean(rubric[r["run_id"]]) for r in ok if r["run_id"] in rubric]
        out[label] = {
            "name": mine[0]["variant_name"] if mine else None,
            "runs": len(mine), "successful": len(ok),
            "failure_rate": (len(mine) - len(ok)) / len(mine) if mine else None,
            "golden_errors": _med(r["golden_errors"] for r in ok),
            "rubric_total": _med(per_run_rubric), "rubric_reviewed_runs": len(per_run_rubric),
            "latency_ms": _med(r["latency_ms"] for r in ok),
            "latency_range_ms": (min(r["latency_ms"] for r in ok), max(r["latency_ms"] for r in ok)) if ok else None,
            "cost_usd": _med(r["cost_usd"] for r in ok),
            "top_tier_share": _med(r.get("top_tier_share") for r in ok),
        }
    return out


def decide(summary: dict, rule: dict) -> tuple[str, list[str]]:
    """'B' (adopt the challenger), 'A' (keep the control) or 'no decision', with the reasons."""
    a, b = summary["A"], summary["B"]
    need = rule.get("min_successful_runs", 3)
    reasons = []
    if a["successful"] < need or b["successful"] < need:
        return "no decision", [f"need {need} successful runs per variant (A {a['successful']}, B {b['successful']})"]
    primary = rule["primary"]
    if a[primary] is None or b[primary] is None:
        return "no decision", [f"no {primary} for both variants yet"
                               + (" (score the packets first)" if primary == "rubric_total" else "")]
    # golden_errors: lower is better, so B's gain is A minus B
    gain = (b[primary] - a[primary]) if primary == "rubric_total" else (a[primary] - b[primary])
    if gain < rule.get("min_gain", 0) or gain <= 0:
        reasons.append(f"{primary}: B's gain {gain:+g} is below the required {rule.get('min_gain', 0)}")
    for metric, key in (("cost_usd", "max_cost_increase_pct"), ("latency_ms", "max_latency_increase_pct")):
        if key in rule and a[metric] and b[metric] is not None:
            pct = (b[metric] - a[metric]) / a[metric] * 100
            if pct > rule[key]:
                reasons.append(f"{metric}: B is {pct:+.0f}% vs A, limit +{rule[key]}%")
    if "max_golden_error_increase" in rule and primary != "golden_errors":
        if b["golden_errors"] - a["golden_errors"] > rule["max_golden_error_increase"]:
            reasons.append(f"golden_errors: B {b['golden_errors']} vs A {a['golden_errors']}")
    if b["failure_rate"] > a["failure_rate"]:
        reasons.append(f"failure rate: B {b['failure_rate']:.0%} vs A {a['failure_rate']:.0%}")
    return ("A", reasons) if reasons else ("B", [f"{primary}: B's gain {gain:+g} meets the rule; guardrails hold"])


def render_report(exp: dict) -> str:
    rows = read_results(exp)
    s = summarize(rows, rubric_totals(exp))
    for label in "AB":
        s[label]["name"] = variant.load(exp["variants"][label])["name"]
    verdict, reasons = decide(s, exp["decision"])
    fmt = lambda v, f="{:g}": "-" if v is None else f.format(v)  # noqa: E731
    lines = [f"Experiment {exp['name']}  (seed: {exp['seed_snapshot']})", "",
             f"{'':24}{'A: ' + str(s['A']['name']):>26}{'B: ' + str(s['B']['name']):>26}"]
    for label, key, f in (("runs (successful)", None, None), ("failure rate", "failure_rate", "{:.0%}"),
                          ("golden errors (median)", "golden_errors", "{:g}"),
                          ("rubric total /30 (median)", "rubric_total", "{:.1f}"),
                          ("  reviewed runs", "rubric_reviewed_runs", "{:g}"),
                          ("latency min (median)", "latency_ms", None), ("cost USD (median)", "cost_usd", "${:.3f}"),
                          ("top-tier sources", "top_tier_share", "{:.0%}")):
        if key is None:
            vals = [f"{s[v]['runs']} ({s[v]['successful']})" for v in "AB"]
        elif key == "latency_ms":
            vals = [fmt(s[v][key] and s[v][key] / 60000, "{:.1f}") for v in "AB"]
        else:
            vals = [fmt(s[v][key], f) for v in "AB"]
        lines.append(f"{label:24}{vals[0]:>26}{vals[1]:>26}")
    lines += ["", f"Decision rule (set before the runs): {json.dumps(exp['decision'])}",
              f"Decision: {verdict}"] + [f"  - {r}" for r in reasons]
    lines += ["", "Per run:"] + [
        f"  {r['variant']}{r['repeat']} {r['run_id']}: {r['status']}, "
        f"{fmt(r['latency_ms'] and r['latency_ms'] / 60000, '{:.1f}')} min, {fmt(r['cost_usd'], '${:.3f}')}, "
        f"golden {fmt(r['golden_errors'])} {r.get('golden_by_check') or ''}" for r in rows]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("run", "packets", "report"):
        print(__doc__)
        return 2
    exp = load_experiment(argv[1])
    if argv[0] == "run":
        print("Results:", run_experiment(exp))
    elif argv[0] == "packets":
        print("Blinded packets:", make_packets(exp))
    else:
        print(render_report(exp))
    return 0
