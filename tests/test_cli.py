from io import StringIO
from uuid import uuid4

from app.cli import main
from app.schemas.schemas import HttpMethod, TestCase, TestCaseType, TestData


def test_cli_exit() -> None:
    stdout = StringIO()
    assert main(stdin=StringIO("2\n"), stdout=stdout) == 0
    assert "Exiting." in stdout.getvalue()


def test_cli_generate_requires_spec_file() -> None:
    stdout = StringIO()
    main(stdin=StringIO("1\n\n2\n"), stdout=stdout)
    assert "SPEC_FILE is required." in stdout.getvalue()
    assert "Exiting." in stdout.getvalue()


def test_cli_generate_scenarios(monkeypatch) -> None:
    case = TestCase(
        name="getPets_happy_path",
        description="List pets",
        endpoint_id=uuid4(),
        endpoint_path="/pets",
        method=HttpMethod.GET,
        test_data=TestData(),
        expected_status=200,
        case_type=TestCaseType.HAPPY_PATH,
    )
    monkeypatch.setattr(
        "app.cli.execute_happy_path_prompt",
        lambda spec_file: [case] if spec_file == "fixtures/petstore.ingest.json" else [],
    )
    stdout = StringIO()
    main(stdin=StringIO("1\nfixtures/petstore.ingest.json\n2\n"), stdout=stdout)
    output = stdout.getvalue()
    assert "getPets_happy_path" in output
    assert "Exiting." in output
