from wire3_gtm.agents import build_agents
from wire3_gtm.tasks import build_tasks


def main() -> None:
    agents = build_agents()
    tasks = build_tasks(agents)
    for name, task in tasks.items():
        print(f"{name:17} -> {task.agent.role:15} output_pydantic={task.output_pydantic.__name__ if task.output_pydantic else None}")


if __name__ == "__main__":
    main()
