from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from uuid import uuid4

from app.cli import DEFAULT_EXPORT_PATH, DEFAULT_RESULTS_PATH, main
from app.schemas.schemas import (
    ExecutionResult,
    HttpMethod,
    TestCase,
    TestCaseType,
    TestData,
    TestResult,
    TestStatus,
)
from app.services.export import export_execution_result, export_test_cases
from app.services.openapi_ingest import PROJECT_ROOT
from app.services.pipeline import GenerationResult, PipelineIssue

EXPORT_PATH = "exports/cli_test_output.json"


def _sample_case() -> TestCase:
    return TestCase(
        name="getPets_happy_path",
        description="List pets",
        endpoint_id=uuid4(),
        endpoint_path="/pets",
        method=HttpMethod.GET,
        test_data=TestData(),
        expected_status=200,
        case_type=TestCaseType.HAPPY_PATH,
    )


def test_cli_exit() -> None:
    stdout = StringIO()
    assert main(stdin=StringIO("4\n"), stdout=stdout) == 0
    assert "Exiting." in stdout.getvalue()


def test_cli_generate_requires_spec_file() -> None:
    stdout = StringIO()
    main(stdin=StringIO("1\n\n4\n"), stdout=stdout)
    assert "SPEC_FILE is required." in stdout.getvalue()
    assert "Exiting." in stdout.getvalue()


def test_cli_generate_scenarios(monkeypatch) -> None:
    case = _sample_case()
    monkeypatch.setattr(
        "app.cli.generate_pipeline",
        lambda spec_file: GenerationResult(cases=[case], contract_count=1, semantic_count=0)
        if spec_file == "fixtures/petstore.ingest.json"
        else GenerationResult(cases=[], contract_count=0, semantic_count=0),
    )
    stdout = StringIO()
    main(stdin=StringIO("1\nfixtures/petstore.ingest.json\n4\n"), stdout=stdout)
    output = stdout.getvalue()
    assert "Generate all tests" in output
    assert "Contract: 1, semantic: 0, kept: 1, dropped: 0" in output
    assert "getPets_happy_path" in output
    assert "Exiting." in output


def test_cli_generate_prints_dropped_reasons(monkeypatch) -> None:
    case = _sample_case()
    monkeypatch.setattr(
        "app.cli.generate_pipeline",
        lambda spec_file: GenerationResult(
            cases=[case],
            contract_count=2,
            semantic_count=1,
            dropped=["createPet_missing_name_dup: duplicate of an earlier scenario"],
        ),
    )
    stdout = StringIO()
    main(stdin=StringIO("1\nfixtures/petstore.ingest.json\n4\n"), stdout=stdout)
    output = stdout.getvalue()
    assert "Contract: 2, semantic: 1, kept: 1, dropped: 1" in output
    assert "Dropped createPet_missing_name_dup: duplicate of an earlier scenario" in output


def test_cli_generate_prints_validation_warnings(monkeypatch) -> None:
    case = _sample_case()
    monkeypatch.setattr(
        "app.cli.generate_pipeline",
        lambda spec_file: GenerationResult(
            cases=[case],
            contract_count=1,
            semantic_count=0,
            validation_warnings=[
                PipelineIssue(
                    severity="warning",
                    code="boundary_mismatch",
                    message="capacity value 50 does not match constraint MAX+1",
                    test_name=case.name,
                    stage="contract",
                    path="capacity",
                    rule="boundary",
                )
            ],
        ),
    )
    stdout = StringIO()
    main(stdin=StringIO("1\nfixtures/petstore.ingest.json\n4\n"), stdout=stdout)
    output = stdout.getvalue()
    assert "Warning getPets_happy_path: capacity value 50 does not match constraint MAX+1" in output


def test_cli_export_requires_generated_scenarios() -> None:
    stdout = StringIO()
    main(stdin=StringIO("2\n\n4\n"), stdout=stdout)
    assert "No scenarios to export" in stdout.getvalue()


def test_cli_export_writes_file(monkeypatch) -> None:
    case = _sample_case()
    monkeypatch.setattr(
        "app.cli.generate_pipeline",
        lambda spec_file: GenerationResult(cases=[case], contract_count=1, semantic_count=0),
    )
    dest = PROJECT_ROOT / EXPORT_PATH
    dest.unlink(missing_ok=True)
    stdout = StringIO()
    try:
        main(
            stdin=StringIO(f"1\nfixtures/petstore.ingest.json\n2\n{EXPORT_PATH}\n4\n"),
            stdout=stdout,
        )
        output = stdout.getvalue()
        assert f"Exported 1 scenario(s) to {dest}" in output
        assert dest.is_file()
        assert "getPets_happy_path" in dest.read_text()
    finally:
        dest.unlink(missing_ok=True)


def test_cli_run_requires_base_url() -> None:
    stdout = StringIO()
    main(stdin=StringIO("3\n\n\n4\n"), stdout=stdout)
    assert "API base URL is required." in stdout.getvalue()


def _passed_suite(case: TestCase) -> ExecutionResult:
    return ExecutionResult(
        base_url="http://localhost:8000",
        started_at=datetime.now(timezone.utc),
        duration_ms=2.0,
        passed=1,
        failed=0,
        error=0,
        skipped=0,
        results=[
            TestResult(
                test_case_id=case.id,
                test_name=case.name,
                status=TestStatus.PASSED,
                expected_status=200,
                actual_status=200,
                url="http://localhost:8000/pets",
            )
        ],
    )


def test_cli_run_scenarios(monkeypatch) -> None:
    case = _sample_case()
    monkeypatch.setattr("app.cli.load_test_cases", lambda path: [case])
    monkeypatch.setattr("app.cli.execute_suite", lambda cases, base_url: _passed_suite(case))
    monkeypatch.setattr("app.cli.export_execution_result", lambda result, path: Path(path))
    stdout = StringIO()
    main(
        stdin=StringIO("3\nexports/happy_path_tests.json\nhttp://localhost:8000\n\n4\n"),
        stdout=stdout,
    )
    output = stdout.getvalue()
    assert "Ran: 1, passed: 1, failed: 0, error: 0, skipped: 0" in output
    assert "getPets_happy_path: passed (status 200)" in output
    assert f"Wrote results to {DEFAULT_RESULTS_PATH}" in output
    assert "Exiting." in output


def test_cli_run_generated_cases(monkeypatch) -> None:
    case = _sample_case()
    monkeypatch.setattr(
        "app.cli.generate_pipeline",
        lambda spec_file: GenerationResult(cases=[case], contract_count=1, semantic_count=0),
    )
    monkeypatch.setattr("app.cli.execute_suite", lambda cases, base_url: _passed_suite(cases[0]))
    monkeypatch.setattr("app.cli.export_execution_result", lambda result, path: Path(path))
    stdout = StringIO()
    main(
        stdin=StringIO("1\nfixtures/petstore.ingest.json\n3\nhttp://localhost:8000\n\n4\n"),
        stdout=stdout,
    )
    output = stdout.getvalue()
    assert "Running 1 generated scenario(s)." in output
    assert "Ran: 1, passed: 1, failed: 0, error: 0, skipped: 0" in output
    assert "getPets_happy_path: passed (status 200)" in output


def test_export_execution_result_writes_json() -> None:
    dest = PROJECT_ROOT / "exports" / "unit_execution_result.json"
    dest.unlink(missing_ok=True)
    try:
        written = export_execution_result(
            _passed_suite(_sample_case()),
            "exports/unit_execution_result.json",
        )
        assert written == dest
        assert dest.is_file()
        assert "getPets_happy_path" in dest.read_text()
        assert DEFAULT_RESULTS_PATH.endswith(".json")
    finally:
        dest.unlink(missing_ok=True)


def test_export_test_cases_writes_json() -> None:
    dest = PROJECT_ROOT / "exports" / "unit_export_test.json"
    dest.unlink(missing_ok=True)
    try:
        written = export_test_cases([_sample_case()], "exports/unit_export_test.json")
        assert written == dest
        assert dest.is_file()
        assert DEFAULT_EXPORT_PATH.endswith(".json")
    finally:
        dest.unlink(missing_ok=True)
