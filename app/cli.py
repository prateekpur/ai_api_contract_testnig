from __future__ import annotations

import json
import sys

from app.env import load_env
from app.schemas.schemas import TestCase
from app.services.export import export_test_cases
from app.services.happy_path_tests import execute_happy_path_prompt

DEFAULT_EXPORT_PATH = "exports/happy_path_tests.json"

MENU = """
AI API Contract Testing
1. Generate contract-based scenarios
2. Export scenarios
3. Exit
"""


def main(stdin=None, stdout=None) -> int:
    load_env()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    generated: list[TestCase] = []
    while True:
        stdout.write(f"{MENU}\nSelect an option: ")
        stdout.flush()
        choice = stdin.readline()
        if choice == "":
            return 0
        choice = choice.strip()
        if choice == "1":
            cases = _generate_scenarios(stdin, stdout)
            if cases is not None:
                generated = cases
        elif choice == "2":
            _export_scenarios(stdin, stdout, generated)
        elif choice == "3":
            stdout.write("Exiting.\n")
            return 0
        else:
            stdout.write("Invalid option. Choose 1, 2, or 3.\n")


def _generate_scenarios(stdin, stdout) -> list[TestCase] | None:
    stdout.write("Path to OpenAPI YAML or ingest JSON file: ")
    stdout.flush()
    spec_file = stdin.readline().strip()
    if not spec_file:
        stdout.write("SPEC_FILE is required.\n")
        return None
    try:
        cases = execute_happy_path_prompt(spec_file)
    except (FileNotFoundError, ValueError, OSError) as exc:
        stdout.write(f"Error: {exc}\n")
        return None
    stdout.write(_format_cases(cases) + "\n")
    return cases


def _export_scenarios(stdin, stdout, cases: list[TestCase]) -> None:
    stdout.write(f"Export path [{DEFAULT_EXPORT_PATH}]: ")
    stdout.flush()
    output_path = stdin.readline().strip() or DEFAULT_EXPORT_PATH
    try:
        dest = export_test_cases(cases, output_path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        stdout.write(f"Error: {exc}\n")
        return
    stdout.write(f"Exported {len(cases)} scenario(s) to {dest}\n")


def _format_cases(cases: list[TestCase]) -> str:
    return json.dumps([case.model_dump(mode="json") for case in cases], indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
