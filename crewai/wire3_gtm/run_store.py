"""Per-run directory that keeps every interim artifact, so a failure late in the
pipeline never costs the research and analysis already paid for.

    runs/<run_id>/
      manifest.json              step statuses, timings, errors
      00_brief.json
      01_plan.json               written the moment the Head Planner finishes
      02_tool_calls.jsonl        one line per MCP call, written as each call returns
      02_evidence_set.json
      02_research_report.json
      03_analyst_artifact.json   03_analyst_report.json
      04_strategy_artifact.json  04_strategy_report.json
      05_document.md/.json/.pdf  05_document_requests.json   (Docs Writer, optional later milestone)
      attempts/                  every guardrail attempt's raw output + feedback,
                                 including drafts that were rejected

Writes are atomic (temp file, then rename), so a crash mid-write cannot leave a
half-written artifact that a later resume would trust.
"""

import json
import os
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

RUNS_DIR = Path(__file__).parent.parent / "runs"

STEPS = ("research", "analyst", "strategy")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return datetime.now().strftime("run-%Y%m%d-%H%M%S")


class RunStore:
    def __init__(self, run_id: str | None = None, root: Path = RUNS_DIR):
        self.run_id = run_id or new_run_id()
        self.dir = root / self.run_id
        (self.dir / "attempts").mkdir(parents=True, exist_ok=True)
        if not self.path("manifest.json").exists():
            self._write_manifest({"run_id": self.run_id, "started_at": _now(), "steps": {}})

    def path(self, name: str) -> Path:
        return self.dir / name

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def save_text(self, name: str, text: str) -> Path:
        target = self.path(name)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, target)  # atomic on the same filesystem
        return target

    def save_json(self, name: str, data) -> Path:
        return self.save_text(name, json.dumps(data, indent=1, default=str))

    def load_text(self, name: str) -> str:
        return self.path(name).read_text()

    def save_attempt(self, step: str, attempt: int, raw: str, feedback) -> None:
        """A guardrail attempt: the model's raw output and why it was (not) accepted.
        This is what survives when the retries are exhausted."""
        self.save_json(
            f"attempts/{step}_attempt_{attempt}.json",
            {"ts": _now(), "accepted": feedback is None, "feedback": feedback, "raw": raw},
        )

    # --- manifest ---------------------------------------------------------
    def manifest(self) -> dict:
        return json.loads(self.load_text("manifest.json"))

    def _write_manifest(self, data: dict) -> None:
        self.save_json("manifest.json", data)

    def _update_step(self, name: str, **fields) -> None:
        m = self.manifest()
        m["steps"].setdefault(name, {}).update(fields)
        m["status"] = (
            "failed" if any(s.get("status") == "failed" for s in m["steps"].values())
            else "complete" if all(
                m["steps"].get(s, {}).get("status") in ("ok", "resumed", "salvaged")
                for s in STEPS + (("docs",) if "docs" in m["steps"] else ())  # docs is an optional later milestone
            )
            else "in_progress"
        )
        self._write_manifest(m)

    def note(self, **fields) -> None:
        m = self.manifest()
        m.update(fields)
        self._write_manifest(m)

    @contextmanager
    def step(self, name: str):
        """Record a step's status and timing. On failure the error is written to
        the manifest and re-raised; artifacts from earlier steps stay on disk."""
        t0 = time.time()
        self._update_step(name, status="running", started_at=_now())
        try:
            yield
        except BaseException as exc:
            self._update_step(
                name, status="failed", ended_at=_now(), seconds=round(time.time() - t0, 1),
                error=f"{type(exc).__name__}: {str(exc)[:500]}",
                traceback=traceback.format_exc()[-1500:],
            )
            raise
        self._update_step(name, status="ok", ended_at=_now(), seconds=round(time.time() - t0, 1), error=None)

    def mark_salvaged(self, name: str, **detail) -> None:
        self._update_step(name, status="salvaged", ended_at=_now(), error=None, **detail)

    def mark_resumed(self, name: str) -> None:
        # A salvaged step stays "salvaged": that is provenance, and "resumed"
        # would hide that its artifact was recovered from a rejected draft.
        if self.manifest()["steps"].get(name, {}).get("status") == "salvaged":
            return
        self._update_step(name, status="resumed", ended_at=_now())
