from io import StringIO
from uuid import uuid4

from app.cli import DEFAULT_EXPORT_PATH, main
from app.schemas.schemas import HttpMethod, TestCase, TestCaseType, TestData
from app.services.export import export_test_cases
from app.services.openapi_ingest import PROJECT_ROOT

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
    assert main(stdin=StringIO("3\n"), stdout=stdout) == 0
    assert "Exiting." in stdout.getvalue()


def test_cli_generate_requires_spec_file() -> None:
    stdout = StringIO()
    main(stdin=StringIO("1\n\n3\n"), stdout=stdout)
    assert "SPEC_FILE is required." in stdout.getvalue()
    assert "Exiting." in stdout.getvalue()


def test_cli_generate_scenarios(monkeypatch) -> None:
    case = _sample_case()
    monkeypatch.setattr(
        "app.cli.execute_happy_path_prompt",
        lambda spec_file: [case] if spec_file == "fixtures/petstore.ingest.json" else [],
    )
    stdout = StringIO()
    main(stdin=StringIO("1\nfixtures/petstore.ingest.json\n3\n"), stdout=stdout)
    output = stdout.getvalue()
    assert "getPets_happy_path" in output
    assert "Exiting." in output


def test_cli_export_requires_generated_scenarios() -> None:
    stdout = StringIO()
    main(stdin=StringIO("2\n\n3\n"), stdout=stdout)
    assert "No scenarios to export" in stdout.getvalue()


def test_cli_export_writes_file(monkeypatch) -> None:
    case = _sample_case()
    monkeypatch.setattr("app.cli.execute_happy_path_prompt", lambda spec_file: [case])
    dest = PROJECT_ROOT / EXPORT_PATH
    dest.unlink(missing_ok=True)
    stdout = StringIO()
    try:
        main(
            stdin=StringIO(f"1\nfixtures/petstore.ingest.json\n2\n{EXPORT_PATH}\n3\n"),
            stdout=stdout,
        )
        output = stdout.getvalue()
        assert f"Exported 1 scenario(s) to {dest}" in output
        assert dest.is_file()
        assert "getPets_happy_path" in dest.read_text()
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
