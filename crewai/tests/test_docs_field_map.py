"""schemas/docs_field_map.json must cover every artifact field exactly, so both
Docs Writers build from the same complete map."""

import json
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"
MAP = json.loads((SCHEMAS / "docs_field_map.json").read_text())

# Cited wrappers and id/enum refs are mapped as one unit, not per sub-key.
LEAF_REFS = {"CitedField", "StrategyCitedField", "DerivedField", "EvidenceIdRef", "CompetitorName", "SupportingRef"}


def _ref_name(node):
    ref = node.get("$ref") or next((a["$ref"] for a in node.get("allOf", []) if "$ref" in a), None)
    return ref.rsplit("/", 1)[-1] if ref else None


def leaf_paths(schema, prefix):
    defs = schema.get("$defs", {})

    def walk(node, path):
        name = _ref_name(node)
        if name in LEAF_REFS:
            yield path
            return
        if name:
            node = defs[name]
        if node.get("type") == "array":
            items = node.get("items", {})
            item_ref = _ref_name(items)
            if items.get("type") == "object" or (item_ref and item_ref not in LEAF_REFS):
                yield from walk(items, path + "[]")
            else:
                yield path  # array of scalars or cited values: one slot
            return
        if "properties" in node:
            for key, sub in node["properties"].items():
                yield from walk(sub, f"{path}.{key}")
            return
        yield path

    yield from walk(schema, prefix)


def schema_paths():
    paths = set()
    for file, prefix in (("analyst_artifact", "analyst"), ("strategy_artifact", "strategy"),
                         ("research_evidence", "evidence")):
        paths |= set(leaf_paths(json.loads((SCHEMAS / f"{file}.schema.json").read_text()), prefix))
    return paths


def test_every_schema_field_is_mapped():
    missing = sorted(schema_paths() - MAP["fields"].keys())
    assert not missing, f"artifact fields with no document section: {missing}"


def test_every_mapped_path_exists_in_a_schema():
    stale = sorted(MAP["fields"].keys() - schema_paths())
    assert not stale, f"map entries for fields no schema defines: {stale}"


def test_entries_name_a_known_section_or_a_reason():
    sections = {s["id"] for s in MAP["sections"]}
    for path, entry in {**MAP["fields"], **MAP["non_artifact_inputs"]}.items():
        if entry["section"] is None:
            assert entry.get("reason"), f"{path}: unrendered field needs a reason"
        else:
            assert entry["section"] in sections, f"{path}: unknown section {entry['section']}"
            assert entry.get("at"), f"{path}: no location"


def test_every_leaf_section_is_filled_by_something():
    # a section with subsections (4, 6, 7, 8) is filled through them
    parents = {s["id"].split(".")[0] for s in MAP["sections"] if "." in s["id"]}
    used = {e["section"] for e in {**MAP["fields"], **MAP["non_artifact_inputs"]}.values()}
    empty = [s["id"] for s in MAP["sections"] if s["id"] not in used | parents | {"A"}]
    assert not empty, f"sections no field fills: {empty}"
