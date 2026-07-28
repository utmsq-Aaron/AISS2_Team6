"""These scripts are run directly, never collected by pytest.

Most of them build a live ``FitDashOrchestrator`` and fire real queries at import
time — merely *collecting* the module would call Strava/Garmin and spend LLM
tokens. So `pytest` (and even `pytest tests/`) stays safe and skips this folder.

To run one, execute it as the script it is:

    .venv/bin/python tests/integration/test_orchestrator.py
"""

collect_ignore_glob = ["test_*.py"]
