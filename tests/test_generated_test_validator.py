from app.prompts.loader import load_api_spec
from app.schemas.schemas import HttpMethod, TestCase, TestCaseType, TestData
from app.services.validation import validate_generated_suite, validate_generated_test


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


def test_valid_happy_path_has_no_violations() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    report = validate_generated_test(
        _create_event("createEvent_happy_path", body=_valid_event_body()),
        spec,
    )
    assert report.ok
    assert report.request_valid is True
    assert report.violations == []


def test_invalid_happy_path_request_is_flagged() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    report = validate_generated_test(
        _create_event(
            "createEvent_happy_path",
            body={**_valid_event_body(), "startDate": "ab"},
        ),
        spec,
    )
    codes = {item.code for item in report.violations}
    assert report.request_valid is False
    assert "format" in codes
    assert "happy_path_invalid_request" in codes
    assert "success_status_invalid_request" in codes
    format_issue = next(item for item in report.violations if item.code == "format")
    assert format_issue.path == "$.startDate"
    assert format_issue.rule == "format"
    assert format_issue.test_name == "createEvent_happy_path"


def test_undeclared_response_status() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    report = validate_generated_test(
        _create_event("createEvent_teapot", body=_valid_event_body(), status=418),
        spec,
    )
    assert any(item.code == "undeclared_status" for item in report.violations)
    assert any(item.rule == "response_status" for item in report.violations)


def test_unknown_endpoint() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    case = TestCase(
        name="createWidget",
        endpoint_path="/widgets",
        method=HttpMethod.POST,
        test_data=TestData(body={}),
        expected_status=201,
    )
    report = validate_generated_test(case, spec)
    assert [item.code for item in report.violations] == ["unknown_endpoint"]
    assert report.request_valid is False


def test_enum_violation() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    report = validate_generated_test(
        _create_event(
            "createEvent_invalid_category",
            body={**_valid_event_body(), "category": "PARTY"},
            status=400,
            case_type=TestCaseType.NEGATIVE,
        ),
        spec,
    )
    enum_issue = next(item for item in report.violations if item.code == "enum")
    assert enum_issue.path == "$.category"
    assert enum_issue.rule == "enum"
    assert report.request_valid is False
    assert "happy_path_invalid_request" not in {item.code for item in report.violations}


def test_pattern_violation_on_path_param() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    case = TestCase(
        name="getEvent_bad_id",
        endpoint_path="/events/{eventId}",
        method=HttpMethod.GET,
        test_data=TestData(path_params={"eventId": "not-an-id"}),
        expected_status=400,
        case_type=TestCaseType.NEGATIVE,
    )
    report = validate_generated_test(case, spec)
    pattern_issue = next(item for item in report.violations if item.code == "pattern")
    assert pattern_issue.path == "path.eventId"
    assert pattern_issue.rule == "pattern"


def test_format_email_violation() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    case = TestCase(
        name="registerAttendee_bad_email",
        endpoint_path="/events/{eventId}/attendees",
        method=HttpMethod.POST,
        test_data=TestData(
            path_params={"eventId": "{{eventId}}"},
            body={"name": "Ada", "email": "not-an-email"},
        ),
        expected_status=400,
        case_type=TestCaseType.NEGATIVE,
    )
    report = validate_generated_test(case, spec)
    format_issue = next(item for item in report.violations if item.code == "format")
    assert format_issue.path == "$.email"
    assert "email" in format_issue.message


def test_boundary_maximum_is_flagged_on_request() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    report = validate_generated_test(
        _create_event(
            "createEvent_capacity_over_max",
            body={**_valid_event_body(), "capacity": 10001},
            status=400,
            case_type=TestCaseType.BOUNDARY,
            mutated_field="capacity",
            constraint="MAX+1",
        ),
        spec,
    )
    assert any(item.code == "maximum" for item in report.violations)
    assert not any(item.code == "boundary_mismatch" for item in report.violations)


def test_boundary_label_mismatch() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    report = validate_generated_test(
        _create_event(
            "createEvent_capacity_max_plus_one",
            body={**_valid_event_body(), "capacity": 50},
            status=400,
            case_type=TestCaseType.BOUNDARY,
            mutated_field="capacity",
            constraint="MAX+1",
        ),
        spec,
    )
    mismatch = next(item for item in report.violations if item.code == "boundary_mismatch")
    assert mismatch.path == "capacity"
    assert "10001" in mismatch.message


def test_min_length_boundary() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    report = validate_generated_test(
        _create_event(
            "createEvent_name_too_short",
            body={**_valid_event_body(), "name": "ab"},
            status=400,
            case_type=TestCaseType.BOUNDARY,
            mutated_field="name",
            constraint="MIN_LENGTH-1",
        ),
        spec,
    )
    assert any(item.code == "minLength" for item in report.violations)
    assert not any(item.code == "boundary_mismatch" for item in report.violations)


def test_happy_path_with_error_status_is_contradictory() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    report = validate_generated_test(
        _create_event(
            "createEvent_happy_path",
            body=_valid_event_body(),
            status=400,
            case_type=TestCaseType.HAPPY_PATH,
        ),
        spec,
    )
    assert any(item.code == "happy_path_error_status" for item in report.violations)


def test_negative_test_with_valid_success_request_is_contradictory() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    report = validate_generated_test(
        _create_event(
            "createEvent_negative",
            body=_valid_event_body(),
            status=201,
            case_type=TestCaseType.NEGATIVE,
        ),
        spec,
    )
    assert any(item.code == "negative_success_status" for item in report.violations)
    assert report.request_valid is True


def test_suite_collects_structured_errors() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    suite = validate_generated_suite(
        [
            _create_event("createEvent_happy_path", body=_valid_event_body()),
            _create_event(
                "createEvent_bad_date",
                body={**_valid_event_body(), "startDate": "ab"},
            ),
        ],
        spec,
    )
    assert suite.ok is False
    assert suite.reports[0].ok
    codes = {item.code for item in suite.violations}
    assert "format" in codes
    assert suite.violations[0].as_dict()["test_name"] == "createEvent_bad_date"
