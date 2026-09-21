"""Trace evidence ids through an AnalystArtifact.

    uv run python -m wire3_gtm.verify_evidence_trace EVIDENCE_SET.json ARTIFACT.json

Levels of assurance, weakest to strongest:
  1. EXISTS   every cited id is in the evidence set          (mechanical; also the guardrail)
  2. ROUTED   a theme's related RQs match the RQs of the evidence it cites
  3. SUPPORTS a cited number / plan name actually appears in the cited excerpts
Only 1 is a hard guarantee. 2 and 3 are heuristics that flag rows for a human.
"""

import json
import re
import sys
from collections import Counter, defaultdict


def walk_cited(node, path=""):
    """Yield (path, value, evidence_ids, basis) for every CitedField-shaped dict."""
    if isinstance(node, dict):
        if {"value", "evidence_ids", "basis"} <= node.keys():
            yield path, node["value"], node["evidence_ids"], node["basis"]
        for k, v in node.items():
            yield from walk_cited(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_cited(v, f"{path}[{i}]")


from wire3_gtm.analyst_checks import numbers_in  # noqa: E402  shared matcher (reads '10 Gbps' as 10000)


def main(ev_path: str, art_path: str) -> None:
    ev = {e["evidence_id"]: e for e in json.load(open(ev_path))["evidence"]}
    art = json.load(open(art_path))
    print(f"evidence set: {len(ev)} records; artifact sections: {len(art)}\n")

    # 1. EXISTS + per-section usage
    per_section = defaultdict(lambda: Counter())
    all_cited: set[str] = set()
    missing = set()
    for section in ("competitor_comparison_table", "pricing_matrix", "product_feature_comparison", "seven_p_analysis"):
        for path, value, ids, basis in walk_cited(art[section], section):
            per_section[section]["fields"] += 1
            per_section[section][basis] += 1
            all_cited.update(ids)
            missing.update(i for i in ids if i not in ev)
    for t in art["market_themes"]:
        per_section["market_themes"]["items"] += 1
        all_cited.update(t["supporting_evidence_ids"]); missing.update(i for i in t["supporting_evidence_ids"] if i not in ev)
    for cat, items in art["swot"].items():
        for it in items:
            per_section["swot"]["items"] += 1; per_section["swot"][it["basis"]] += 1
            all_cited.update(it["evidence_ids"]); missing.update(i for i in it["evidence_ids"] if i not in ev)
    print("1. EXISTS:", "PASS" if not missing else f"FAIL {sorted(missing)[:5]}",
          f"({len(all_cited)} distinct ids cited, {len(ev) - len(all_cited & set(ev))} records never cited)")
    for sec, c in per_section.items():
        print(f"   {sec:28} {dict(c)}")

    # 2. ROUTED: themes
    print("\n2. ROUTED (theme RQs vs RQs of cited evidence):")
    for t in art["market_themes"]:
        claimed = set(t["related_research_question_ids"])
        actual = Counter(ev[i]["research_question_id"] for i in t["supporting_evidence_ids"] if i in ev)
        off = claimed - set(actual)
        print(f"   {t['theme_id']} claims {sorted(claimed)}; cited evidence is from {dict(actual)}"
              + (f"  <-- RQs with no cited evidence: {sorted(off)}" if off else ""))

    # 3. SUPPORTS: numbers and names in evidence-basis fields
    print("\n3. SUPPORTS (does the cited excerpt contain the value?):")
    hits = miss = 0
    misses = []
    for section in ("competitor_comparison_table", "pricing_matrix"):
        for path, value, ids, basis in walk_cited(art[section], section):
            if basis != "evidence" or isinstance(value, bool) or value is None:
                continue
            text = " ".join(ev[i]["excerpt"] + " " + ev[i]["claim"] for i in ids if i in ev)
            ok = (float(value) in numbers_in(text)) if isinstance(value, (int, float)) \
                else (str(value).lower() in text.lower())
            hits += ok; miss += not ok
            if not ok:
                misses.append((path, value, ids[:2]))
    print(f"   {hits} values found in cited text, {miss} not found")
    for path, value, ids in misses[:12]:
        print(f"   NOT FOUND  {path} = {value!r}  cited {ids}")

    # source quality of what was cited
    print("\nsource types cited:", dict(Counter(ev[i]["source_type"] for i in all_cited if i in ev)))
    print("RQs with cited evidence:", sorted({ev[i]["research_question_id"] for i in all_cited if i in ev}))


if __name__ == "__main__":
    main(*sys.argv[1:3])
