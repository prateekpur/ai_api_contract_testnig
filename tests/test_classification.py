from app.prompts.loader import load_api_spec
from app.schemas.schemas import Determinism, HttpMethod, TestCase, TestCaseType, TestData, TestSource
from app.services.classification import classify_generated_test
from app.services.pipeline import apply_quality_gate, finalize_cases
from app.services.validation import request_is_valid


def _event_body(**overrides: object) -> dict:
    body = {
        "name": "Summit",
        "category": "CONFERENCE",
        "startDate": "2026-11-01T10:00:00Z",
        "capacity": 100,
    }
    body.update(overrides)
    return body


def _policy_body(**overrides: object) -> dict:
    body = {
        "customerId": 1,
        "policyType": "AUTO",
        "effectiveDate": "2026-01-01",
        "expirationDate": "2027-01-01",
        "premium": 1200.5,
    }
    body.update(overrides)
    return body


def _case(
    name: str,
    path: str,
    method: HttpMethod,
    *,
    status: int,
    case_type: TestCaseType = TestCaseType.HAPPY_PATH,
    body: object | None = None,
    path_params: dict | None = None,
    description: str | None = None,
) -> TestCase:
    return TestCase(
        name=name,
        description=description,
        endpoint_path=path,
        method=method,
        test_data=TestData(body=body, path_params=path_params or {}),
        expected_status=status,
        case_type=case_type,
    )


def test_event_happy_path_is_deterministic_contract() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    case = _case(
        "createEvent_happy_path",
        "/events",
        HttpMethod.POST,
        status=201,
        body=_event_body(),
    )
    kept, dropped = apply_quality_gate([case], spec)
    assert dropped == []
    assert kept[0].test_source == TestSource.CONTRACT
    assert kept[0].determinism == Determinism.DETERMINISTIC
    assert kept[0].classification_reason
    assert kept[0].expected_status == 201
    assert kept[0].test_data.body == _event_body()


def test_event_missing_name_400_is_inferred_contract() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    body = _event_body()
    body.pop("name")
    case = _case(
        "createEvent_missing_name",
        "/events",
        HttpMethod.POST,
        status=400,
        case_type=TestCaseType.NEGATIVE,
        body=body,
    )
    kept, dropped = apply_quality_gate([case], spec)
    assert dropped == []
    assert kept[0].test_source == TestSource.CONTRACT
    assert kept[0].determinism == Determinism.INFERRED
    assert "schema violation" in (kept[0].classification_reason or "")


def test_event_valid_body_409_is_insufficient_spec() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    case = _case(
        "createEvent_duplicate",
        "/events",
        HttpMethod.POST,
        status=409,
        case_type=TestCaseType.NEGATIVE,
        body=_event_body(),
    )
    kept, dropped = apply_quality_gate([case], spec)
    assert dropped == []
    assert kept[0].test_source == TestSource.CONTRACT
    assert kept[0].determinism == Determinism.UNKNOWN
    assert "does not determine" in (kept[0].classification_reason or "")
    assert kept[0].expected_status == 409
    assert kept[0].test_data.body == _event_body()


def test_petstore_semantic_create_is_inferred_semantic() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    contract = [
        _case(
            "createPet_happy_path",
            "/pets",
            HttpMethod.POST,
            status=201,
            body={"name": "ab", "species": "DOG"},
        )
    ]
    semantic = [
        _case(
            "createPet_create_resource_then_retrieve",
            "/pets",
            HttpMethod.POST,
            status=201,
            body={"name": "cd", "species": "CAT"},
        )
    ]
    result = finalize_cases(contract, semantic, spec)
    names = {case.name: case for case in result.cases}
    semantic_case = names["createPet_create_resource_then_retrieve"]
    assert semantic_case.test_source == TestSource.SEMANTIC_INFERENCE
    assert semantic_case.determinism == Determinism.INFERRED
    assert semantic_case.classification_reason
    for case in result.cases:
        assert case.determinism is not None
        assert case.test_source is not None
        assert case.classification_reason


def test_sql_injection_name_is_conditional_security() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    case = _case(
        "createPet_sql_injection",
        "/pets",
        HttpMethod.POST,
        status=201,
        body={"name": "ab", "species": "DOG"},
    )
    kept, dropped = apply_quality_gate([case], spec)
    assert dropped == []
    assert kept[0].test_source == TestSource.SECURITY_INFERENCE
    assert kept[0].determinism == Determinism.CONDITIONAL
    assert kept[0].expected_status == 201
    assert kept[0].case_type == TestCaseType.HAPPY_PATH
    assert kept[0].test_data.body == {"name": "ab", "species": "DOG"}


def test_insurance_valid_201_is_deterministic_and_409_is_unknown() -> None:
    _, spec = load_api_spec("sample_specs/insurance_api.yaml")
    happy = _case(
        "createPolicy_happy_path",
        "/policies",
        HttpMethod.POST,
        status=201,
        body=_policy_body(),
    )
    conflict = _case(
        "createPolicy_duplicate",
        "/policies",
        HttpMethod.POST,
        status=409,
        case_type=TestCaseType.NEGATIVE,
        body=_policy_body(),
    )
    result = finalize_cases([happy, conflict], [], spec)
    names = {case.name: case for case in result.cases}
    assert names["createPolicy_happy_path"].test_source == TestSource.CONTRACT
    assert names["createPolicy_happy_path"].determinism == Determinism.DETERMINISTIC
    assert names["createPolicy_duplicate"].test_source == TestSource.CONTRACT
    assert names["createPolicy_duplicate"].determinism == Determinism.UNKNOWN
    assert names["createPolicy_duplicate"].expected_status == 409


def test_classifier_matches_quality_gate_for_event_happy_path() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    endpoint = next(
        item for item in spec.endpoints if item.path == "/events" and item.method == HttpMethod.POST
    )
    case = _case(
        "createEvent_happy_path",
        "/events",
        HttpMethod.POST,
        status=201,
        body=_event_body(),
    )
    valid = request_is_valid(endpoint, case.test_data, spec.schemas)
    labeled = classify_generated_test(case, spec, endpoint, valid)
    assert labeled.test_source == TestSource.CONTRACT
    assert labeled.determinism == Determinism.DETERMINISTIC
    assert labeled.reason
