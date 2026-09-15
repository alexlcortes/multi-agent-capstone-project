from dotenv import load_dotenv
from openai import OpenAI
from crewai import Agent, Crew, Task

load_dotenv()


def test_raw_openai_call() -> None:
    """Isolates 'does the API key work' from 'does CrewAI work'."""
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Reply with exactly: pong"}],
    )
    print("Raw OpenAI call:", response.choices[0].message.content)


def test_crewai_call() -> None:
    """Confirms CrewAI itself is wired to the same key end to end."""
    agent = Agent(
        role="Smoke Tester",
        goal="Reply with exactly the word pong and nothing else.",
        backstory="You only ever say pong.",
        llm="gpt-4o-mini",
    )
    task = Task(
        description="Reply with exactly: pong",
        expected_output="The single word: pong",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task])
    result = crew.kickoff()
    print("CrewAI call:", result.raw)


def main() -> None:
    test_raw_openai_call()
    test_crewai_call()


if __name__ == "__main__":
    main()
