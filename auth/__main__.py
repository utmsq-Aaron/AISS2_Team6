"""Convenience launcher for the one-off auth/setup flows.

Usage from the repo root:
    python -m auth all
    python -m auth garmin
    python -m auth strava
    python -m auth gmail
    python -m auth calendar

The ``calendar`` flow writes the single-user token the calendar MCP server
reads; the app's Settings page offers the same connect for deployments whose
callback URL is registered with the OAuth client.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
AUTH_DIR = REPO_ROOT / "auth"

COMMANDS = {
    "garmin": AUTH_DIR / "garmin_setup.py",
    "strava": AUTH_DIR / "strava_oauth.py",
    "gmail": AUTH_DIR / "google_oauth.py",
    "google": AUTH_DIR / "google_oauth.py",
    "calendar": AUTH_DIR / "google_calendar.py",
}


def run_script(script_path: Path) -> int:
    completed = subprocess.run([sys.executable, str(script_path)], cwd=REPO_ROOT)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FitDash auth/setup helpers")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("all", *COMMANDS.keys()),
        default="all",
        help="Which auth flow to run (default: all one-off setup flows)",
    )
    args = parser.parse_args()

    if args.command == "all":
        for name in ("garmin", "strava", "gmail"):
            print(f"\n=== {name} ===")
            code = run_script(COMMANDS[name])
            if code != 0:
                return code
        return 0

    return run_script(COMMANDS[args.command])


if __name__ == "__main__":
    raise SystemExit(main())
