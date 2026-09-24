"""Equity screen for the Strategy output, for briefs with framing rules (not Ocala).

A keyword screen, not a legal review: it flags statements that describe places by
crime or safety, or that recommend excluding, deprioritizing or charging more
based on income, housing type, age, language or other demographics. Flags are
reported (in the document's "Equity and compliance check" section and the run
summary) for a person to review; they do not block the run, because a screen
this simple will sometimes flag a sentence that is fine.
"""

import re

from wire3_gtm.analyst_models import dump_contract
from wire3_gtm.strategy_models import StrategyArtifact

STIGMA = re.compile(
    r"\b(crime|criminal|high[- ]crime|unsafe|dangerous|bad neighbou?rhoods?|rough (?:area|part)s?|ghetto|slums?|"
    r"blighted|sketchy|undesirable (?:area|neighbou?rhood)s?)\b", re.I)
TREATMENT = re.compile(
    r"\b(exclud\w*|avoid(?:ing)? (?:marketing|targeting|serving|building|selling|offering)|deprioriti[sz]\w*|skip\w*|redlin\w*|not (?:serve|offer|build)|withhold\w*|"
    r"lower priority|last in line|higher (?:price|prices|rate|rates|fees?|deposits?)|surcharg\w*|"
    r"worse (?:terms|service|speeds?))\b", re.I)
GROUP = re.compile(
    r"\b(low[- ]income|poor|poverty|renters?|tenants?|apartments?|multifamily|manufactured|mobile[- ]homes?|"
    r"seniors?|elderly|retirees?|older (?:residents|households|adults)|hispanic|latino|latinx|spanish[- ]speaking|"
    r"non[- ]english|immigrants?|minority|minorities|black|african[- ]american|race|racial|ethnic\w*|"
    r"neighbou?rhoods? with)\b", re.I)
NEGATION = re.compile(r"\b(not|never|no|without|avoid(?:ing)? any|must not|do not|don't|rather than)\b[^.;]{0,40}$", re.I)
WINDOW = 80  # characters between a treatment word and a group word to count as one recommendation


def _statements(strategy: StrategyArtifact):
    """(where, text) for every string the Strategy agent wrote."""
    def walk(o, path):
        if isinstance(o, dict):
            label = next((o[k] for k in ("icp_id", "pain_id", "pillar_id", "channel_id", "phase_id", "metric_id",
                                         "risk_id", "frq_id") if k in o), None)
            for k, v in o.items():
                yield from walk(v, f"{label or path}.{k}" if k != "value" else (label or path))
        elif isinstance(o, list):
            for v in o:
                yield from walk(v, path)
        elif isinstance(o, str) and len(o) > 3 and not re.fullmatch(r"[A-Z]+-[\w-]+", o):
            yield path, o
    yield from walk(dump_contract(strategy), "strategy")


# Descriptions of the market rather than recommendations: a pain ("renters pay higher prices after the
# promo") or a risk usually describes competitors, not how Wire3 would treat anyone. Mitigations are checked.
NOT_RECOMMENDATIONS = re.compile(r"^(PAIN-\d+|FRQ-\d+|RISK-\d+\.(statement|likelihood|impact))\b")


def equity_flags(strategy: StrategyArtifact) -> list[dict]:
    """[{where, text, reason}] for recommendations a person should review. Empty means nothing was flagged."""
    flags = []
    for where, text in _statements(strategy):
        if NOT_RECOMMENDATIONS.match(where):
            continue
        if m := STIGMA.search(text):
            flags.append({"where": where, "text": text[:300], "reason": f"describes a place or group as {m.group(0)!r}"})
            continue
        for t in TREATMENT.finditer(text):
            if NEGATION.search(text[:t.start()]):
                continue  # "do not exclude renters" is the rule, not a violation
            near = text[max(0, t.start() - WINDOW):t.end() + WINDOW]
            if g := GROUP.search(near):
                flags.append({"where": where, "text": text[:300],
                              "reason": f"{t.group(0)!r} near {g.group(0)!r}: check it does not mean worse "
                                        f"availability, price or service for that group"})
                break
    return flags


def statements_checked(strategy: StrategyArtifact) -> int:
    return sum(1 for where, _ in _statements(strategy) if not NOT_RECOMMENDATIONS.match(where))
