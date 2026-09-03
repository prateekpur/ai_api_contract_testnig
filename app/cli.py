from __future__ import annotations

import json
import sys

from app.env import load_env
from app.schemas.schemas import TestCase
from app.services.happy_path_tests import execute_happy_path_prompt

MENU = """
AI API Contract Testing
1. Generate contract-based scenarios
2. Exit
"""


def main(stdin=None, stdout=None) -> int:
    load_env()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    while True:
        stdout.write(f"{MENU}\nSelect an option: ")
        stdout.flush()
        choice = stdin.readline()
        if choice == "":
            return 0
        choice = choice.strip()
        if choice == "1":
            _generate_scenarios(stdin, stdout)
        elif choice == "2":
            stdout.write("Exiting.\n")
            return 0
        else:
            stdout.write("Invalid option. Choose 1 or 2.\n")


def _generate_scenarios(stdin, stdout) -> None:
    stdout.write("Path to ingest JSON file: ")
    stdout.flush()
    spec_file = stdin.readline().strip()
    if not spec_file:
        stdout.write("SPEC_FILE is required.\n")
        return
    try:
        cases = execute_happy_path_prompt(spec_file)
    except (FileNotFoundError, ValueError, OSError) as exc:
        stdout.write(f"Error: {exc}\n")
        return
    stdout.write(_format_cases(cases) + "\n")


def _format_cases(cases: list[TestCase]) -> str:
    return json.dumps([case.model_dump(mode="json") for case in cases], indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
