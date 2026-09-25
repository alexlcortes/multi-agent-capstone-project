"""Pipeline vocabulary in reader-facing text.

The Analyst and Strategy text fields go into the plan word for word, so a reader
should never meet the pipeline's own names for things. Live run LAKE-20260924-233944
shipped "do not mention mobile bundles in the EvidenceSet; developer brief requires
Wire3 mobile-bundle field to be brief_stated false." Across the saved runs the Analyst
wrote "EvidenceSet" / "evidence set" 77 times (its input is called the EvidenceSet).

Both guardrails send a draft back once for these (with any other problems, or on
their own); a draft that still has them after that is accepted and the terms are
reported, so wording alone never costs more than one retry or fails a run.
"""

import re

# (pattern, what to write instead). Checked in text with URLs removed.
_TERMS = [
    (re.compile(r"\bevidence ?set\b", re.I), 'say "the research" or name the source'),
    (re.compile(r"\bdeveloper brief\b", re.I), 'say "the project brief"'),
    (re.compile(r"\b(?:analyst|strategy) artifact\b|\bEvidenceRecord\b", re.I), 'say "the analysis" or "the research"'),
    # a field, basis or enum name: brief_stated, evidence_id, has_mobile_bundle
    (re.compile(r"(?<![\w/.:@-])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w/@-]|\.\w)"), "write it in plain words"),
]
_URL = re.compile(r"https?://\S+|www\.\S+")
# Written by code, not the model: the 12-month blend formula names its inputs on purpose.
_SKIP_KEYS = {"formula"}


def internal_terms(doc) -> list[dict]:
    """Every pipeline term in a prose field of a dumped artifact: [{path, term, fix}]. A string with no
    space is an id, enum value or URL, not prose, and is skipped."""
    found: list[dict] = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k not in _SKIP_KEYS:
                    walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str) and " " in node:
            text = _URL.sub("", node)
            hits = sorted((m.start(), m.group(0), fix) for rx, fix in _TERMS for m in rx.finditer(text))
            found.extend({"path": path, "term": term, "fix": fix} for _, term, fix in hits)

    walk(doc, "")
    return found


def wording_feedback(found: list[dict], limit: int = 10) -> str:
    """One guardrail problem naming each place to rewrite."""
    places = "; ".join(f"'{f['term']}' in {f['path']} ({f['fix']})" for f in found[:limit])
    more = f"; and {len(found) - limit} more" if len(found) > limit else ""
    return ("Text fields are read by business readers of the final plan: rewrite them without the pipeline's "
            f"own vocabulary: {places}{more}")
