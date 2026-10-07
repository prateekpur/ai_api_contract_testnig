from app.prompts.loader import load_api_spec
from app.schemas.schemas import HttpMethod, TestCase, TestCaseType, TestData
from app.services.happy_path_tests import generate_pipeline
from app.services.pipeline import finalize_cases


def _valid_event_body() -> dict:
    return {
        "name": "Summit",
        "category": "CONFERENCE",
        "startDate": "2026-11-01T10:00:00Z",
        "capacity": 100,
    }


def _create_event(
    name: str,
    *,
    body: dict,
    status: int = 201,
    case_type: TestCaseType = TestCaseType.HAPPY_PATH,
    mutated_field: str | None = None,
    constraint: str | None = None,
) -> TestCase:
    return TestCase(
        name=name,
        endpoint_path="/events",
        method=HttpMethod.POST,
        test_data=TestData(body=body),
        expected_status=status,
        case_type=case_type,
        mutated_field=mutated_field,
        constraint=constraint,
    )


def test_pipeline_rejects_invalid_contract_and_keeps_valid_semantic() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    contract = [
        _create_event(
            "createEvent_happy_path",
            body={**_valid_event_body(), "startDate": "ab"},
        )
    ]
    semantic = [_create_event("createEvent_for_get", body=_valid_event_body())]
    result = finalize_cases(contract, semantic, spec)
    assert [case.name for case in result.cases] == ["createEvent_for_get"]
    assert result.contract_count == 1
    assert result.semantic_count == 1
    assert any(issue.test_name == "createEvent_happy_path" for issue in result.validation_errors)
    assert any(issue.code == "success_status_invalid_request" for issue in result.validation_errors)
    assert any(issue.stage == "contract" for issue in result.validation_errors)
    assert any("createEvent_happy_path" in item for item in result.dropped)


def test_pipeline_preserves_valid_tests_and_expected_behavior() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    body = _valid_event_body()
    contract = [_create_event("createEvent_happy_path", body=body)]
    result = finalize_cases(contract, [], spec)
    assert result.validation_errors == []
    assert result.dropped == []
    kept = result.cases[0]
    assert kept.name == "createEvent_happy_path"
    assert kept.expected_status == 201
    assert kept.case_type == TestCaseType.HAPPY_PATH
    assert kept.test_data.body == body
    assert kept.endpoint_path == "/events"
    assert kept.method == HttpMethod.POST


def test_pipeline_does_not_rewrite_invalid_happy_path_into_a_negative() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    invalid = _create_event(
        "createEvent_happy_path",
        body={**_valid_event_body(), "startDate": "ab"},
        status=201,
        case_type=TestCaseType.HAPPY_PATH,
    )
    result = finalize_cases([invalid], [], spec)
    assert result.cases == []
    assert not any(case.expected_status == 400 for case in result.cases)
    assert not any(case.case_type == TestCaseType.NEGATIVE for case in result.cases)


def test_pipeline_keeps_negative_schema_tests_without_warnings() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    body = _valid_event_body()
    body.pop("name")
    case = _create_event(
        "createEvent_missing_name",
        body=body,
        status=400,
        case_type=TestCaseType.NEGATIVE,
    )
    result = finalize_cases([case], [], spec)
    assert [item.name for item in result.cases] == ["createEvent_missing_name"]
    assert result.cases[0].expected_status == 400
    assert result.cases[0].case_type == TestCaseType.NEGATIVE
    assert result.validation_errors == []
    assert result.validation_warnings == []


def test_pipeline_flags_boundary_mismatch_without_dropping() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    case = _create_event(
        "createEvent_capacity_max_plus_one",
        body={**_valid_event_body(), "capacity": 50},
        status=400,
        case_type=TestCaseType.BOUNDARY,
        mutated_field="capacity",
        constraint="MAX+1",
    )
    result = finalize_cases([case], [], spec)
    assert [item.name for item in result.cases] == ["createEvent_capacity_max_plus_one"]
    assert result.cases[0].expected_status == 400
    assert result.cases[0].test_data.body["capacity"] == 50
    assert result.validation_errors == []
    assert [issue.code for issue in result.validation_warnings] == ["boundary_mismatch"]
    assert result.validation_warnings[0].test_name == "createEvent_capacity_max_plus_one"


def test_generate_pipeline_runs_validation_after_both_tracks(monkeypatch) -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    contract = [
        _create_event(
            "createEvent_happy_path",
            body={**_valid_event_body(), "startDate": "ab"},
        )
    ]
    semantic = [_create_event("createEvent_for_get", body=_valid_event_body())]

    def fake_execute(spec_file, load_prompt, *, model=None, label="prompt"):
        return contract if label == "contract-tests" else semantic

    monkeypatch.setattr("app.services.happy_path_tests._execute_prompt", fake_execute)
    result = generate_pipeline("sample_specs/arbitary_api.yaml")
    assert spec.title
    assert [case.name for case in result.cases] == ["createEvent_for_get"]
    assert result.validation_errors
    assert result.validation_warnings == []
