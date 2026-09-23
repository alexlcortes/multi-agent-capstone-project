"""Blinded review packets for KPI 4 (strategy quality): the documents of the runs the KPIs count, from both
implementations, shuffled and made to look alike, for scoring with eval/rubric.md.

    uv run python -m wire3_gtm review-packets     # -> eval/reviews/kpi4/packets/Doc-NN.md + key.json

Only presentation is normalized, never content: the same header and legend for every document, no bold
markup, no run ids. Content that only one implementation writes (CrewAI's evidence-quality notes in section
2) stays, because it is part of what is being judged; a reviewer may still recognize it.
"""

import json
import random
import re
from pathlib import Path

from wire3_gtm import kpi

OUT = kpi.REPO / "eval" / "reviews" / "kpi4"
CRITERIA = ("clarity", "feasibility", "differentiation", "evidence_quality", "citation_completeness", "usefulness")
LEGEND = ("How to read citations: bracketed EV- ids are sources, listed in Appendix B with links; THEME-, SWOT-, "
          "ASM- and UNK- ids are analyst findings, listed in Appendix A; [inference] marks judgment with no direct "
          "source; [brief] marks a fact given in the project brief.")


def normalize(markdown: str, label: str) -> str:
    """Same presentation for both implementations; content unchanged."""
    lines = [ln for ln in markdown.splitlines() if not ln.startswith("<!--")]
    out, header_done, legend_done = [], False, False
    for ln in lines:
        if not header_done and ln.startswith("Run: "):
            sources = re.search(r"Sources cited: (\d+)", ln)
            out.append(f"{label} | Sources cited: {sources.group(1) if sources else '?'}")
            header_done = True
            continue
        if not legend_done and "How to read citations" in ln:
            out.append(LEGEND)
            legend_done = True
            continue
        out.append(ln.replace("**", ""))
    return "\n".join(out).rstrip() + "\n"


def documents() -> list[tuple[str, str, str]]:
    """(implementation, run id, markdown) for every run the KPIs count."""
    runs, _ = kpi.completed_runs()
    return [(r.impl, r.run_id, r.markdown) for r in runs if r.kind == "full"]


def make(seed: int | None = None, out: Path = OUT) -> Path:
    pdir = out / "packets"
    if (pdir / "key.json").exists():
        raise FileExistsError(f"{pdir}/key.json exists: packets are made once, so scores stay matched to the key")
    docs = documents()
    seed = seed if seed is not None else random.SystemRandom().randrange(2**32)
    random.Random(seed).shuffle(docs)
    pdir.mkdir(parents=True)
    key = {}
    for i, (impl, run_id, md) in enumerate(docs, 1):
        label = f"Doc-{i:02d}"
        (pdir / f"{label}.md").write_text(normalize(md, label))
        key[label] = {"implementation": impl, "run_id": run_id}
    (pdir / "key.json").write_text(json.dumps({"seed": seed, "key": key}, indent=1))
    template = {"reviewer": "", "reviewed_at": "", "rubric": "eval/rubric.md", "blind": True,
                "notes": "Score each Doc against eval/rubric.md: 1-5 per criterion, one line of evidence each.",
                "scores": {label: {c: {"score": None, "evidence": ""} for c in CRITERIA} for label in key}}
    (out / "TEMPLATE.json").write_text(json.dumps(template, indent=1))
    return pdir


def main(argv: list[str]) -> int:
    pdir = make()
    print(f"{len(list(pdir.glob('Doc-*.md')))} blinded documents -> {pdir}\n"
          f"Scoring form: {OUT / 'TEMPLATE.json'} (copy it to {OUT}/<date>-<your name>.json)\n"
          f"Do not open {pdir / 'key.json'} until scoring is done.")
    return 0
