"""uv run python -m wire3_gtm            # list agents and tasks (no LLM calls)
uv run python -m wire3_gtm run          # full pipeline, artifacts kept in runs/<run_id>/
uv run python -m wire3_gtm run RUN_ID   # resume: finished steps are loaded from disk
uv run python -m wire3_gtm salvage RUN_ID  # recover a failed Analyst step from its saved drafts
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

        store = RunStore(sys.argv[2] if len(sys.argv) > 2 else None)
        print(f"Run {store.run_id}: artifacts are saved as each step finishes in {store.dir}")
        try:
            result = run_pipeline(load_brief(), store=store)
        except BaseException:
            print(f"\nRun failed; finished steps are kept. Resume with:\n"
                  f"  uv run python -m wire3_gtm run {store.run_id}", file=sys.stderr)
            raise
        print(f"Run {result.run_id} complete: {result.run_dir}")
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
