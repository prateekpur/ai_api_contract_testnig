from __future__ import annotations

import json
import sys

from app.env import load_env
from app.schemas.schemas import TestCase, TestResult
from app.services.export import export_test_cases
from app.services.happy_path_tests import generate_pipeline
from app.services.runner import execute_workflow, load_test_cases

DEFAULT_EXPORT_PATH = "exports/happy_path_tests.json"

MENU = """
AI API Contract Testing
1. Generate all tests
2. Export scenarios
3. Run scenarios
4. Exit
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
            _run_scenarios(stdin, stdout)
        elif choice == "4":
            stdout.write("Exiting.\n")
            return 0
        else:
            stdout.write("Invalid option. Choose 1, 2, 3, or 4.\n")


def _generate_scenarios(stdin, stdout) -> list[TestCase] | None:
    stdout.write("Path to OpenAPI YAML or ingest JSON file: ")
    stdout.flush()
    spec_file = stdin.readline().strip()
    if not spec_file:
        stdout.write("SPEC_FILE is required.\n")
        return None
    stdout.write("Generating contract and semantic tests...\n")
    stdout.flush()
    try:
        result = generate_pipeline(spec_file)
    except (FileNotFoundError, ValueError, OSError) as exc:
        stdout.write(f"Error: {exc}\n")
        return None
    stdout.write(
        f"Contract: {result.contract_count}, semantic: {result.semantic_count}, "
        f"kept: {result.kept_count}, dropped: {len(result.dropped)}\n"
    )
    for reason in result.dropped:
        stdout.write(f"Dropped {reason}\n")
    stdout.write(_format_cases(result.cases) + "\n")
    return result.cases


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


def _run_scenarios(stdin, stdout) -> None:
    stdout.write(f"Test file [{DEFAULT_EXPORT_PATH}]: ")
    stdout.flush()
    path = stdin.readline().strip() or DEFAULT_EXPORT_PATH
    stdout.write("API base URL: ")
    stdout.flush()
    base_url = stdin.readline().strip()
    if not base_url:
        stdout.write("API base URL is required.\n")
        return
    try:
        cases = load_test_cases(path)
        results = execute_workflow(cases, base_url)
    except (FileNotFoundError, ValueError, OSError) as exc:
        stdout.write(f"Error: {exc}\n")
        return
    stdout.write(_format_results(cases, results) + "\n")


def _format_cases(cases: list[TestCase]) -> str:
    return json.dumps([case.model_dump(mode="json") for case in cases], indent=2)


def _format_results(cases: list[TestCase], results: list[TestResult]) -> str:
    names = {case.id: case.name for case in cases}
    lines: list[str] = []
    for result in results:
        name = names.get(result.test_case_id, str(result.test_case_id))
        actual = result.actual_status if result.actual_status is not None else "-"
        line = f"{name}: {result.status.value} (status {actual})"
        if result.errors:
            line += " — " + "; ".join(result.errors)
        lines.append(line)
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
