"""A/B variants: one YAML file per variant, selected for a run with the WIRE3_VARIANT env var.

    name: strict-sources                  # required; recorded with every result
    hypothesis: >                         # required; what this variant should change, and why
      Asking the Analyst to prefer primary sources raises top-tier share toward the brief's 80%.
    model: gpt-5-mini                     # optional; default agents.DEFAULT_MODEL
    pricing_usd_per_1m:                   # required when model is not in run_log.PRICING
      {input: 0.25, cached_input: 0.025, output: 2.0, verified: "2026-09-19"}
    agents:                               # optional; fields of config/agents.yaml
      analyst: {backstory: {append: "..."}}
    tasks:                                # optional; fields of config/tasks.yaml
      analyze_evidence: {description: {append: "..."}}

Only prompt text and the model can vary. Anything else in a variant file is an error, so a variant
cannot silently change what the pipeline does (budgets, guardrails, tools) and be credited for it.
No variant set means the committed configuration, unchanged.
"""

import os
from pathlib import Path

import yaml

ENV = "WIRE3_VARIANT"
AGENT_FIELDS = {"role", "goal", "backstory"}
TASK_FIELDS = {"description", "expected_output"}
TOP_LEVEL = {"name", "hypothesis", "model", "pricing_usd_per_1m", "agents", "tasks"}
PRICE_KEYS = {"input", "cached_input", "output", "verified"}


class VariantError(ValueError):
    pass


def load(path: str | Path) -> dict:
    v = yaml.safe_load(Path(path).read_text()) or {}
    if unknown := set(v) - TOP_LEVEL:
        raise VariantError(f"{path}: unknown keys {sorted(unknown)} (a variant may change only prompts and the model)")
    for key in ("name", "hypothesis"):
        if not str(v.get(key) or "").strip():
            raise VariantError(f"{path}: '{key}' is required")
    if (p := v.get("pricing_usd_per_1m")) is not None and set(p) != PRICE_KEYS:
        raise VariantError(f"{path}: pricing_usd_per_1m needs exactly {sorted(PRICE_KEYS)} (USD per 1M tokens, and "
                           "the date you checked the provider's price list)")
    for section, allowed in (("agents", AGENT_FIELDS), ("tasks", TASK_FIELDS)):
        for key, fields in (v.get(section) or {}).items():
            if bad := set(fields) - allowed:
                raise VariantError(f"{path}: {section}.{key} may only change {sorted(allowed)}, not {sorted(bad)}")
            for field, edit in fields.items():
                if not isinstance(edit, dict) or len(edit) != 1 or next(iter(edit)) not in ("append", "replace"):
                    raise VariantError(f"{path}: {section}.{key}.{field} must be {{append: text}} or {{replace: text}}")
    return v


def active() -> dict | None:
    path = os.environ.get(ENV)
    return load(path) if path else None


def model(default: str) -> str:
    v = active()
    return (v or {}).get("model") or default


def pricing() -> dict | None:
    return (active() or {}).get("pricing_usd_per_1m")


def _apply(config: dict, edits: dict, section: str) -> dict:
    out = {k: dict(spec) for k, spec in config.items()}
    for key, fields in edits.items():
        if key not in out:
            raise VariantError(f"variant edits {section}.{key}, which is not in config/{section}.yaml")
        for field, edit in fields.items():
            (op, text), = edit.items()
            out[key][field] = text if op == "replace" else f"{str(out[key].get(field) or '').rstrip()}\n\n{text}"
    return out


def apply_agents(config: dict) -> dict:
    return _apply(config, (active() or {}).get("agents") or {}, "agents")


def apply_tasks(config: dict) -> dict:
    return _apply(config, (active() or {}).get("tasks") or {}, "tasks")
