"""uv run python -m wire3_gtm            # list agents and tasks (no LLM calls)
uv run python -m wire3_gtm run          # full pipeline, artifacts kept in runs/<run_id>/
uv run python -m wire3_gtm run --docs google   # ...then write and verify the Google Doc (or --docs local)
uv run python -m wire3_gtm run RUN_ID   # resume: finished steps are loaded from disk
uv run python -m wire3_gtm compare      # latest n8n vs CrewAI run, side by side
uv run python -m wire3_gtm salvage RUN_ID  # recover a failed Analyst step from its saved drafts
uv run python -m wire3_gtm salvage-research RUN_ID  # rebuild evidence from searches already saved (no new searches)
uv run python -m wire3_gtm docs RUN_ID     # Docs Writer, local Markdown only (no Google)
uv run python -m wire3_gtm docs RUN_ID --google   # also create, verify and export the Google Doc
uv run python -m wire3_gtm google-auth     # one-time Google consent (opens a browser)
"""

import sys

from wire3_gtm.agents import build_agents
from wire3_gtm.tasks import build_tasks


def main() -> None:
    if sys.argv[1:2] == ["run"]:
        from wire3_gtm.pipeline import load_brief, run_pipeline
        from wire3_gtm.run_store import RunStore

        args = sys.argv[2:]
        docs = args[args.index("--docs") + 1] if "--docs" in args else None
        positional = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--docs")]
        store = RunStore(positional[0] if positional else None)
        print(f"Run {store.run_id}: artifacts are saved as each step finishes in {store.dir}")
        try:
            result = run_pipeline(load_brief(), store=store, docs=docs)
        except BaseException:
            print(f"\nRun failed; finished steps are kept. Resume with:\n"
                  f"  uv run python -m wire3_gtm run {store.run_id}" + (f" --docs {docs}" if docs else ""), file=sys.stderr)
            raise
        finally:  # the summary is written even when the run failed
            summary = store.path("06_run_summary.txt")
            if summary.exists():
                print("\n" + summary.read_text())
        return
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
