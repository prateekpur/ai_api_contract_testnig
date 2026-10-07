from app.prompts.loader import load_api_spec
from app.schemas.schemas import Determinism, HttpMethod, TestCase, TestCaseType, TestData, TestSource
from app.services.pipeline import apply_quality_gate, finalize_cases
from app.services.validation import validate_request


def _event_create(
    name: str,
    *,
    body: dict,
    status: int = 201,
    case_type: TestCaseType = TestCaseType.HAPPY_PATH,
) -> TestCase:
    return TestCase(
        name=name,
        endpoint_path="/events",
        method=HttpMethod.POST,
        test_data=TestData(body=body),
        expected_status=status,
        case_type=case_type,
    )


def _valid_event_body() -> dict:
    return {
        "name": "Summit",
        "category": "CONFERENCE",
        "startDate": "2026-11-01T10:00:00Z",
        "capacity": 100,
    }


def test_invalid_datetime_is_not_a_valid_request() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    endpoint = next(
        item for item in spec.endpoints if item.path == "/events" and item.method == HttpMethod.POST
    )
    errors = validate_request(
        endpoint,
        TestData(body={**_valid_event_body(), "startDate": "ab"}),
        spec.schemas,
    )
    assert any("date-time" in error for error in errors)


def test_quality_gate_drops_happy_path_with_invalid_datetime() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    case = _event_create("createEvent_happy_path", body={**_valid_event_body(), "startDate": "ab"})
    kept, dropped = apply_quality_gate([case], spec)
    assert kept == []
    assert "createEvent_happy_path" in dropped[0]
    assert "expects success" in dropped[0]


def test_quality_gate_keeps_valid_happy_path() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    case = _event_create("createEvent_happy_path", body=_valid_event_body())
    kept, dropped = apply_quality_gate([case], spec)
    assert [item.name for item in kept] == ["createEvent_happy_path"]
    assert dropped == []
    assert kept[0].happy_path_contract_valid is True
    assert kept[0].determinism == Determinism.DETERMINISTIC
    assert kept[0].test_source == TestSource.CONTRACT
    assert kept[0].expected_schema_ref == "Event"


def test_schema_violation_error_is_inferred_not_deterministic() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    body = _valid_event_body()
    body.pop("name")
    case = _event_create(
        "createEvent_missing_name",
        body=body,
        status=400,
        case_type=TestCaseType.NEGATIVE,
    )
    kept, dropped = apply_quality_gate([case], spec)
    assert [item.name for item in kept] == ["createEvent_missing_name"]
    assert dropped == []
    assert kept[0].happy_path_contract_valid is False
    assert kept[0].determinism == Determinism.INFERRED


def test_empty_update_body_is_structurally_valid() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    case = TestCase(
        name="updateEvent_empty_body",
        endpoint_path="/events/{eventId}",
        method=HttpMethod.PUT,
        test_data=TestData(path_params={"eventId": "{{eventId}}"}, body={}),
        expected_status=200,
        case_type=TestCaseType.HAPPY_PATH,
        dependencies=[],
    )
    kept, dropped = apply_quality_gate([case], spec)
    assert dropped == []
    assert kept[0].happy_path_contract_valid is True


def test_finalize_stamps_semantic_source() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    contract = [
        TestCase(
            name="createPet_happy_path",
            endpoint_path="/pets",
            method=HttpMethod.POST,
            test_data=TestData(body={"name": "ab", "species": "DOG"}),
            expected_status=201,
            case_type=TestCaseType.HAPPY_PATH,
        )
    ]
    semantic = [
        TestCase(
            name="createPet_create_resource_then_retrieve",
            endpoint_path="/pets",
            method=HttpMethod.POST,
            test_data=TestData(body={"name": "cd", "species": "CAT"}),
            expected_status=201,
            case_type=TestCaseType.HAPPY_PATH,
        )
    ]
    result = finalize_cases(contract, semantic, spec)
    names = {case.name: case for case in result.cases}
    assert names["createPet_happy_path"].test_source == TestSource.CONTRACT
    assert names["createPet_happy_path"].determinism == Determinism.DETERMINISTIC
    assert names["createPet_create_resource_then_retrieve"].test_source == (
        TestSource.SEMANTIC_INFERENCE
    )
    assert names["createPet_create_resource_then_retrieve"].determinism == Determinism.INFERRED
