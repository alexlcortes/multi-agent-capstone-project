import pytest


@pytest.fixture(autouse=True)
def _never_write_the_real_run_log(tmp_path_factory, monkeypatch):
    """Tests must not append to crewai/logs/runs.jsonl, the submission's run record."""
    from wire3_gtm import run_log

    monkeypatch.setattr(run_log, "LOG_PATH", tmp_path_factory.mktemp("logs") / "runs.jsonl")
