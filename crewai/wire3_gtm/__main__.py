"""uv run python -m wire3_gtm            # list agents and tasks (no LLM calls)
uv run python -m wire3_gtm run          # full pipeline, artifacts kept in runs/<run_id>/
uv run python -m wire3_gtm run --docs google   # ...then write and verify the Google Doc (or --docs local)
uv run python -m wire3_gtm run --budget max_cost_usd=0.02   # test a cap: tighter limit for this run only
uv run python -m wire3_gtm run RUN_ID   # resume: finished steps are loaded from disk
uv run python -m wire3_gtm compare      # latest n8n vs CrewAI run, side by side
uv run python -m wire3_gtm salvage RUN_ID  # recover a failed Analyst step from its saved drafts
uv run python -m wire3_gtm salvage-research RUN_ID  # rebuild evidence from searches already saved (no new searches)
uv run python -m wire3_gtm docs RUN_ID     # Docs Writer, local Markdown only (no Google)
uv run python -m wire3_gtm docs RUN_ID --google   # also create, verify and export the Google Doc
uv run python -m wire3_gtm executive-brief RUN_ID   # short CEO version of a finished run's plan (local Markdown, no LLM)
uv run python -m wire3_gtm google-auth     # one-time Google consent (opens a browser)
uv run python -m wire3_gtm snapshot RUN_ID  # save a validated Strategy output to snapshots/RUN_ID (no Google)
uv run python -m wire3_gtm run-records     # add a run_record to every logged run that lacks one
uv run python -m wire3_gtm ab run|packets|report EXPERIMENT.yaml   # prompt/model A/B (see wire3_gtm/ab.py)
uv run python -m wire3_gtm kpi [--recheck-links]   # the six capstone KPIs for both implementations
uv run python -m wire3_gtm review-packets   # blinded documents of the counted runs, for the KPI 4 rubric review
"""

import sys

from wire3_gtm.agents import build_agents
from wire3_gtm.tasks import build_tasks


def main() -> None:
    if sys.argv[1:2] == ["run"]:
        from wire3_gtm.pipeline import load_brief, run_pipeline
        from wire3_gtm.run_store import RunStore

        from wire3_gtm.run_log import Budget, RunMonitor

        args = sys.argv[2:]
        valued = {"--docs", "--budget"}
        docs = args[args.index("--docs") + 1] if "--docs" in args else None
        overrides = [args[i + 1] for i, a in enumerate(args) if a == "--budget"]
        positional = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] not in valued)]
        store = RunStore(positional[0] if positional else None)
        brief = load_brief()
        monitor = RunMonitor(store, Budget.from_brief(brief))
        monitor.budget_overrides = monitor.budget.override(overrides)
        if monitor.budget_overrides:
            print("Budget overridden for this run:", monitor.budget_overrides)
        print(f"Run {store.run_id}: artifacts are saved as each step finishes in {store.dir}")
        try:
            result = run_pipeline(brief, store=store, docs=docs, monitor=monitor)
        except BaseException:
            print(f"\nRun failed; finished steps are kept. Resume with:\n"
                  f"  uv run python -m wire3_gtm run {store.run_id}" + (f" --docs {docs}" if docs else ""), file=sys.stderr)
            raise
        finally:  # the summary is written even when the run failed
            summary = store.path("06_run_summary.txt")
            if summary.exists():
                print("\n" + summary.read_text())
        return
    if sys.argv[1:2] == ["review-packets"]:
        from wire3_gtm.review_packets import main as review_packets

        raise SystemExit(review_packets(sys.argv[2:]))
    if sys.argv[1:2] == ["kpi"]:
        from wire3_gtm.kpi import main as kpi

        raise SystemExit(kpi(sys.argv[2:]))
    if sys.argv[1:2] == ["ab"]:
        from wire3_gtm.ab import main as ab

        raise SystemExit(ab(sys.argv[2:]))
    if sys.argv[1:2] == ["compare"]:
        from wire3_gtm.compare_logs import main as compare

        raise SystemExit(compare(sys.argv[2:]))
    if sys.argv[1:2] == ["salvage-research"]:
        from wire3_gtm.pipeline import salvage_research
        from wire3_gtm.run_store import RunStore

        print("Salvaged:", salvage_research(RunStore(sys.argv[2])))
        return
    if sys.argv[1:2] == ["salvage"]:
        from wire3_gtm.pipeline import salvage_analyst
        from wire3_gtm.run_store import RunStore

        print("Salvaged:", salvage_analyst(RunStore(sys.argv[2])))
        return
    if sys.argv[1:2] == ["google-auth"]:
        from wire3_gtm.docs_google import authorize

        print("Token saved to", authorize())
        return
    if sys.argv[1:2] == ["snapshot"]:
        from wire3_gtm.run_store import RUNS_DIR, RunStore
        from wire3_gtm.snapshot import export_snapshot

        if not (RUNS_DIR / sys.argv[2]).is_dir():  # RunStore() would create an empty run
            raise SystemExit(f"no run {sys.argv[2]} in {RUNS_DIR}")
        print("Snapshot saved to", export_snapshot(RunStore(sys.argv[2])))
        return
    if sys.argv[1:2] == ["run-records"]:
        import json

        from wire3_gtm.run_log import LOG_PATH
        from wire3_gtm.run_record import backfill
        from wire3_gtm.run_store import RUNS_DIR

        def brief_for(cid):
            p = RUNS_DIR / cid / "00_brief.json"
            return json.loads(p.read_text()) if p.exists() else None

        print("run_record added for:", backfill(LOG_PATH, brief_for) or "none (all runs have one)")
        return
    if sys.argv[1:2] == ["executive-brief"]:
        from wire3_gtm.executive_brief import write_executive_brief
        from wire3_gtm.run_store import RUNS_DIR, RunStore

        if not (RUNS_DIR / sys.argv[2]).is_dir():  # RunStore() would create an empty run
            raise SystemExit(f"no run {sys.argv[2]} in {RUNS_DIR}")
        print("Executive brief:", write_executive_brief(RunStore(sys.argv[2])))
        return
    if sys.argv[1:2] == ["docs"]:
        from wire3_gtm.pipeline import run_docs
        from wire3_gtm.run_store import RunStore

        result = run_docs(RunStore(sys.argv[2]), "google" if "--google" in sys.argv else "local")
        print(result)
        return
    agents = build_agents()
    for name, task in build_tasks(agents).items():
        out = task.output_pydantic.__name__ if task.output_pydantic else None
        print(f"{name:17} -> {task.agent.role:15} output_pydantic={out}")


if __name__ == "__main__":
    main()
