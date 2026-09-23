"""The six capstone KPIs, measured the same way for both implementations from saved run data.

    uv run python -m wire3_gtm kpi                 # compute from saved data -> eval/kpi/results.json
    uv run python -m wire3_gtm kpi --recheck-links # also re-check every cited URL today (network)

Definitions, data and limitations are written up in eval/KPI_REPORT.md; this module is the calculation.
Runs counted: completed runs of the CURRENT brief (brief_id of brief.json). Earlier brief versions are
listed but never counted.
"""

import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from wire3_gtm.run_record import brief_id as brief_id_of

REPO = Path(__file__).resolve().parents[2]
KPI_DIR = REPO / "eval" / "kpi"
CREW_RUNS = REPO / "crewai" / "runs"
CREW_LOG = REPO / "crewai" / "logs" / "runs.jsonl"
N8N_LOG = REPO / "n8n" / "logs" / "runs.jsonl"
EV_RE = re.compile(r"\bEV-[0-9a-f]{8}\b")
TOP = ("primary", "top_secondary")

TARGETS = {"coverage": 0.90, "top_tier_share": 0.80, "broken_link_share": 0.0, "latency_minutes": 15.0,
           "strategy_quality": 4.0, "reproducibility": 0.80}

# n8n: the one completed run of the current brief whose artifacts survive (execution 29)
N8N_RUNS = {"client-mudhx5in-ji99rx": {"data": KPI_DIR / "data" / "n8n_client-mudhx5in-ji99rx.json",
                                        "document": REPO / "eval" / "inputs" / "n8n_client-mudhx5in-ji99rx.md"}}


# --- source tiers ----------------------------------------------------------------------------------

def _norm(url: str) -> tuple[str, str]:
    p = urlparse(url.strip())
    host = p.netloc.lower().removeprefix("www.")
    return host, f"https://{host}{p.path}" + (f"?{p.query}" if p.query else "")


def load_tiers(path: Path = KPI_DIR / "source_tiers.yaml") -> dict:
    return yaml.safe_load(path.read_text())


def tier_of(url: str, tiers: dict) -> tuple[str, str]:
    """(tier, reason). Unclassified is an error: every source is classified on purpose."""
    host, norm = _norm(url)
    for rule in tiers["urls"]:
        if norm.startswith(rule["prefix"].replace("://www.", "://")):
            return rule["tier"], rule["why"]
    rule = tiers["domains"].get(host)
    if rule is None:
        raise KeyError(f"no tier rule for {host} ({url}): add it to eval/kpi/source_tiers.yaml")
    return rule["tier"], rule["why"]


# --- runs --------------------------------------------------------------------------------------------

@dataclass
class Run:
    impl: str
    run_id: str
    evidence: list[dict]
    analyst: dict
    strategy: dict
    markdown: str
    link_check: dict | None
    record: dict | None
    kind: str = "full"  # full | seeded (research reused from a snapshot)
    extra: dict = field(default_factory=dict)


def run_records(log: Path) -> dict[str, dict]:
    out = {}
    for line in log.read_text().splitlines():
        if '"run_record"' in line:
            r = json.loads(line)
            if r.get("event_type") == "run_record":
                out[r["run_id"]] = r
    return out


def current_brief_id() -> str:
    return brief_id_of(json.loads((REPO / "brief.json").read_text()))


def load_crewai(run_id: str, records: dict) -> Run:
    d = CREW_RUNS / run_id
    read = lambda n: json.loads((d / n).read_text()) if (d / n).exists() else None  # noqa: E731
    return Run("crewai", run_id, read("02_evidence_set.json")["evidence"], read("03_analyst_artifact.json"),
               read("04_strategy_artifact.json"), (d / "05_document.md").read_text(), read("06_link_check.json"),
               records.get(run_id), "seeded" if run_id.startswith("ab-") else "full")


def load_n8n(run_id: str, records: dict) -> Run:
    src = N8N_RUNS[run_id]
    data = json.loads(src["data"].read_text())
    return Run("n8n", run_id, data["evidence"], data["analyst"], data["strategy"], src["document"].read_text(),
               None, records.get(run_id))


def completed_runs() -> tuple[list[Run], list[dict]]:
    """Completed runs of the current brief with saved artifacts, and every run record (for latency, cost and
    reliability, which also count failed runs)."""
    bid = current_brief_id()
    crew_recs, n8n_recs = run_records(CREW_LOG), run_records(N8N_LOG)
    runs = []
    for rid, r in crew_recs.items():
        seeded_ok = not rid.startswith("ab-") or rid in ab_runs()
        if r.get("brief_id") == bid and r["status"] in ("success", "degraded") and seeded_ok \
                and (CREW_RUNS / rid / "05_document.md").exists():
            runs.append(load_crewai(rid, crew_recs))
    for rid in N8N_RUNS:
        runs.append(load_n8n(rid, n8n_recs))
    all_recs = [{**r, "impl": "crewai"} for r in crew_recs.values()] + [{**r, "impl": "n8n"} for r in n8n_recs.values()]
    return runs, all_recs


def cited_ids(run: Run) -> set[str]:
    known = {e["evidence_id"] for e in run.evidence}
    return {i for i in EV_RE.findall(run.markdown) if i in known}


# --- KPI 1: coverage -----------------------------------------------------------------------------------

def coverage(run: Run, rq_ids: list[str]) -> dict:
    """A research question is covered when the final document cites at least one search result that was
    returned for it (any copy: one result is stored once per question it served)."""
    key = lambda e: (e["source_url"], e.get("excerpt"))  # noqa: E731
    rqs_of: dict = {}
    for e in run.evidence:
        if e.get("research_question_id"):
            rqs_of.setdefault(key(e), set()).add(e["research_question_id"])
    by_id = {e["evidence_id"]: e for e in run.evidence}
    covered = {rq for i in cited_ids(run) for rq in rqs_of.get(key(by_id[i]), ())}
    per_rq = {rq: rq in covered for rq in rq_ids}
    return {"value": sum(per_rq.values()) / len(rq_ids), "per_rq": per_rq}


# --- KPI 2: source quality ----------------------------------------------------------------------------

def source_quality(run: Run, tiers: dict, recheck: dict | None = None) -> dict:
    """Counting rule: distinct URLs cited in the final document (a page stored as several evidence records
    counts once). Top-tier share = primary + top_secondary over all distinct cited URLs. Broken = a 404/410
    or unresolvable host (links.classify); blocked/unverified are reported, not counted as broken."""
    by_id = {e["evidence_id"]: e for e in run.evidence}
    ids = cited_ids(run)
    urls = sorted({by_id[i]["source_url"] for i in ids})
    t = {u: tier_of(u, tiers)[0] for u in urls}
    n = len(urls)
    rec_tiers = Counter(tier_of(by_id[i]["source_url"], tiers)[0] for i in ids)
    out = {
        "cited_urls": n, "cited_records": len(ids),
        "by_tier": dict(Counter(t.values())),
        "top_tier_share": sum(v in TOP for v in t.values()) / n if n else None,
        "top_tier_share_by_record": sum(rec_tiers[k] for k in TOP) / len(ids) if ids else None,
        "weak_urls": sorted(u for u, v in t.items() if v == "weak"),
    }
    if run.link_check and run.link_check.get("status") != "unchecked":
        broken = {b["url"] for b in run.link_check.get("broken", [])} & set(urls)
        out["run_time_link_check"] = {"broken": len(broken), "broken_share": len(broken) / n if n else None,
                                      "blocked_or_unverified": len((set(run.link_check.get("blocked_urls", []))
                                                                    | set(run.link_check.get("unverified_urls", []))) & set(urls))}
    else:
        out["run_time_link_check"] = None
    if recheck is not None:
        classes = Counter(recheck[u]["class"] for u in urls)
        out["recheck"] = {"broken": classes.get("broken", 0), "broken_share": classes.get("broken", 0) / n if n else None,
                          "by_class": dict(classes),
                          "broken_urls": sorted(u for u in urls if recheck[u]["class"] == "broken")}
    return out


def recheck_links(urls: list[str]) -> dict[str, dict]:
    """Today's check of each URL, same rules as the pipeline's validate_source + links.classify: HEAD with
    redirects. A 405 or 403 on HEAD is retried as GET, since some servers refuse HEAD only."""
    import httpx

    from wire3_gtm.links import check_links

    def validate(u: str) -> dict:
        try:
            r = httpx.head(u, timeout=10.0, follow_redirects=True)
            if r.status_code in (403, 405):
                r = httpx.get(u, timeout=10.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
            return {"is_valid": r.status_code < 400, "status_code": r.status_code, "error": None}
        except httpx.HTTPError as exc:
            return {"is_valid": False, "status_code": None, "error": str(exc)}

    report = check_links(urls, validate)
    out = {u: {"class": "ok"} for u in report["ok_urls"]}
    for key, cls in (("blocked_urls", "blocked"), ("unverified_urls", "unverified"), ("unchecked_urls", "unchecked")):
        out.update({u: {"class": cls} for u in report[key]})
    out.update({b["url"]: {"class": "broken", "status_code": b["status_code"], "error": b["error"]} for b in report["broken"]})
    out.update({u: {"class": "broken", "error": "malformed"} for u in report["malformed"]})
    return out


# --- KPI 3 and 6: latency and cost (from run records, all runs of the current brief) -------------------

def latency_and_cost(records: list[dict], impl: str, budget_usd: float) -> dict:
    bid = current_brief_id()
    mine = [r for r in records if r["impl"] == impl and r.get("brief_id") == bid
            and not r["run_id"].startswith("ab-") and not (r.get("issues") and any("budget overridden" in i for i in r["issues"]))]
    done = [r for r in mine if r["status"] in ("success", "degraded")]
    mins = [r["duration_ms"] / 60000 for r in done]
    costs = [(r.get("cost") or {}).get("estimated_llm_usd") for r in done]
    return {
        "runs_attempted": len(mine), "runs_completed": len(done),
        "statuses": dict(Counter(r["status"] for r in mine)),
        "latency_minutes": {r["run_id"]: round(r["duration_ms"] / 60000, 1) for r in done},
        "latency_max": round(max(mins), 1) if mins else None,
        "latency_median": round(statistics.median(mins), 1) if mins else None,
        "cost_usd": {r["run_id"]: c for r, c in zip(done, costs)},
        "cost_max": max(c for c in costs if c is not None) if any(c is not None for c in costs) else None,
        "cost_is_lower_bound": any((r.get("cost") or {}).get("is_lower_bound") for r in done),
        "budget_usd": budget_usd,
        "excluded": "runs with a deliberately overridden budget (cap tests) and seeded A/B runs",
    }


# --- KPI 4: strategy quality ---------------------------------------------------------------------------

CRITERIA = ("clarity", "feasibility", "differentiation", "evidence_quality", "citation_completeness", "usefulness")


def strategy_quality(reviews_dir: Path = REPO / "eval" / "reviews") -> dict:
    """Mean of the six rubric criteria (1-5) per implementation, per review file. Human and model reviews are
    kept apart: only human, blind reviews count towards the KPI."""
    out = {}
    for f in sorted(reviews_dir.glob("*.json")):
        if f.name == "TEMPLATE.json":
            continue
        rev = json.loads(f.read_text())
        is_model = "model" in rev.get("reviewer", "").lower() or "claude" in rev.get("reviewer", "").lower()
        for impl, scores in rev.get("scores", {}).items():
            vals = [(scores.get(c) or {}).get("score") for c in CRITERIA]
            if all(isinstance(v, int) for v in vals):
                out.setdefault(impl, []).append({"review": f.name, "mean": round(sum(vals) / 6, 2),
                                                 "counts_toward_kpi": not is_model and bool(rev.get("blind"))})
    return out


# --- KPI 5: reproducibility --------------------------------------------------------------------------

def decision_items(run: Run) -> dict[str, object]:
    """The answers a reader would act on, as comparable values. Free text is left out on purpose: two good
    runs never share wording, so wording is not what reproducibility means here."""
    a, s = run.analyst, run.strategy
    v = lambda f: (f or {}).get("value") if isinstance(f, dict) else f  # noqa: E731
    items = {}
    for row in a["competitor_comparison_table"]:
        c = row["competitor_name"]
        items[f"{c}: footprint confirmed"] = v(row["footprint_confirmed_in_region"])
        items[f"{c}: service type"] = str(v(row["service_type"])).lower()
        items[f"{c}: mobile bundle"] = v(row["has_mobile_bundle_available"])
    for c in ("Spectrum", "AT&T", "T-Mobile Home Internet"):
        rows = [r for r in a["pricing_matrix"] if r["competitor_name"] == c]
        items[f"{c}: priced plans (promo, post-promo)"] = tuple(sorted(
            (v(r["promo_price_usd_per_month"]), v(r["post_promo_price_usd_per_month"])) for r in rows))
    items["strategy: channel categories"] = tuple(sorted({ch["channel_category"] for ch in s["channels"]}))
    items["strategy: number of ICPs"] = len(s["icps"])
    return items


def ab_runs(results: Path = REPO / "eval" / "ab" / "analyst-sources" / "results.jsonl") -> set[str]:
    return {json.loads(line)["run_id"] for line in results.read_text().splitlines() if line.strip()} if results.exists() else set()


def ab_control_runs(results: Path = REPO / "eval" / "ab" / "analyst-sources" / "results.jsonl") -> set[str]:
    """The finished A/B experiment's control runs: same evidence, same committed prompts. The aborted first
    attempt (results.aborted-*.jsonl) ran on older code and is not used."""
    if not results.exists():
        return set()
    return {json.loads(line)["run_id"] for line in results.read_text().splitlines()
            if line.strip() and json.loads(line)["variant"] == "A"}


def reproducibility(runs: list[Run]) -> dict | None:
    """Mean, over decision items, of the share of runs that give the most common answer. 1.0 = every run
    agrees on every item. Needs 3+ runs."""
    if len(runs) < 3:
        return None
    per_run = [decision_items(r) for r in runs]
    keys = sorted(set().union(*per_run))
    agreement = {}
    for k in keys:
        answers = [json.dumps(p.get(k), default=str) for p in per_run]
        agreement[k] = Counter(answers).most_common(1)[0][1] / len(answers)
    return {"runs": [r.run_id for r in runs], "value": round(statistics.mean(agreement.values()), 3),
            "per_item": {k: round(v, 2) for k, v in agreement.items()}}


# --- all together --------------------------------------------------------------------------------------

def measure(recheck: bool = False) -> dict:
    brief = json.loads((REPO / "brief.json").read_text())
    tiers = load_tiers()
    runs, records = completed_runs()
    rq_ids = [f"RQ{i + 1}" for i in range(len(brief["research_questions"]))]
    checks = recheck_links(sorted({e["source_url"] for r in runs for e in r.evidence
                                   if e["evidence_id"] in cited_ids(r)})) if recheck else None
    per_run = {r.run_id: {"impl": r.impl, "kind": r.kind,
                          "coverage": coverage(r, rq_ids), "source_quality": source_quality(r, tiers, checks)}
               for r in runs}
    out = {"brief_id": current_brief_id(), "targets": TARGETS, "per_run": per_run,
           "strategy_quality": strategy_quality(), "links_rechecked": recheck}
    for impl in ("crewai", "n8n"):
        full = [r for r in runs if r.impl == impl and r.kind == "full"]
        seeded_control = [r for r in runs if r.impl == impl and r.run_id in ab_control_runs()]
        out[impl] = {
            "latency_and_cost": latency_and_cost(records, impl, brief["budget"]["max_cost_usd"]),
            "reproducibility_end_to_end": reproducibility(full),
            "reproducibility_fixed_evidence": reproducibility(seeded_control),
            "full_runs_measured": [r.run_id for r in full],
        }
    return out


def classify_all() -> dict:
    """Every evidence record of every counted run, with its tier: the audit trail for KPI 2."""
    tiers = load_tiers()
    runs, _ = completed_runs()
    rows, seen = [], set()
    for r in runs:
        for e in r.evidence:
            t, why = tier_of(e["source_url"], tiers)
            rows.append({"impl": r.impl, "run_id": r.run_id, "evidence_id": e["evidence_id"],
                         "url": e["source_url"], "tier": t, "why": why})
            seen.add(e["source_url"])
    return {"records": rows, "distinct_urls": len(seen)}


def main(argv: list[str]) -> int:
    result = measure(recheck="--recheck-links" in argv)
    KPI_DIR.mkdir(parents=True, exist_ok=True)
    (KPI_DIR / "results.json").write_text(json.dumps(result, indent=1, default=str))
    audit = classify_all()
    with (KPI_DIR / "evidence_tiers.csv").open("w") as f:
        f.write("impl,run_id,evidence_id,tier,url,why\n")
        for row in audit["records"]:
            f.write(",".join(json.dumps(str(row[k])) for k in ("impl", "run_id", "evidence_id", "tier", "url", "why")) + "\n")
    print(f"KPI results -> {KPI_DIR / 'results.json'}; {len(audit['records'])} records "
          f"({audit['distinct_urls']} distinct URLs) classified -> {KPI_DIR / 'evidence_tiers.csv'}")
    return 0
