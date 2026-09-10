from app.prompts.loader import load_api_spec
from app.schemas.schemas import Dependency, HttpMethod, TestCase, TestCaseType, TestData
from app.services.pipeline import case_fingerprint, dedupe_cases, finalize_cases


def _case(
    name: str,
    *,
    path: str = "/pets",
    method: HttpMethod = HttpMethod.POST,
    status: int = 400,
    body: object | None = None,
    path_params: dict | None = None,
    headers: dict | None = None,
    dependencies: list[Dependency] | None = None,
    case_type: TestCaseType = TestCaseType.NEGATIVE,
    description: str | None = None,
) -> TestCase:
    data: dict = {"path_params": path_params or {}, "body": body}
    if headers is not None:
        data["headers"] = headers
    return TestCase(
        name=name,
        description=description,
        endpoint_path=path,
        method=method,
        test_data=TestData(**data),
        expected_status=status,
        case_type=case_type,
        dependencies=dependencies or [],
    )


def test_scenario_fingerprint_ignores_name_and_description() -> None:
    first = _case("createPet_missing_name", body={"species": "DOG"}, description="omit name")
    second = _case("createPet_no_name", body={"species": "DOG"}, description="missing name field")
    assert case_fingerprint(first) == case_fingerprint(second)


def test_scenario_fingerprint_ignores_body_key_order() -> None:
    first = _case(
        "createPet_happy_path",
        status=201,
        body={"name": "ab", "species": "DOG"},
        case_type=TestCaseType.HAPPY_PATH,
    )
    second = _case(
        "createPet_for_get",
        status=201,
        body={"species": "DOG", "name": "ab"},
        case_type=TestCaseType.HAPPY_PATH,
    )
    assert case_fingerprint(first) == case_fingerprint(second)


def test_scenario_fingerprint_treats_null_body_as_missing() -> None:
    first = TestCase(
        name="getPets_happy_path",
        endpoint_path="/pets",
        method=HttpMethod.GET,
        test_data=TestData(),
        expected_status=200,
        case_type=TestCaseType.HAPPY_PATH,
    )
    second = TestCase(
        name="listPets",
        endpoint_path="/pets",
        method=HttpMethod.GET,
        test_data=TestData(body=None),
        expected_status=200,
        case_type=TestCaseType.HAPPY_PATH,
    )
    assert case_fingerprint(first) == case_fingerprint(second)


def test_scenario_fingerprint_canonicalizes_nested_body_placeholders() -> None:
    first = _case("create_with_id_ref", body={"id": "{{petId}}"})
    second = _case("create_with_id_alias", body={"id": "{{id}}"})
    assert case_fingerprint(first) == case_fingerprint(second)


def test_scenario_fingerprint_keeps_different_status_or_body() -> None:
    missing_name = _case("createPet_missing_name", body={"species": "DOG"}, status=400)
    happy = _case(
        "createPet_happy_path",
        status=201,
        body={"name": "ab", "species": "DOG"},
        case_type=TestCaseType.HAPPY_PATH,
    )
    other_body = _case(
        "createPet_for_get",
        status=201,
        body={"name": "cd", "species": "CAT"},
        case_type=TestCaseType.HAPPY_PATH,
    )
    assert case_fingerprint(missing_name) != case_fingerprint(happy)
    assert case_fingerprint(happy) != case_fingerprint(other_body)


def test_scenario_dedupe_keeps_contract_over_semantic_near_duplicate() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    contract = [
        _case(
            "getPet_happy_path",
            path="/pets/{petId}",
            method=HttpMethod.GET,
            status=200,
            path_params={"petId": "{{petId}}"},
            dependencies=[
                Dependency(
                    source_test="createPet_happy_path",
                    source_path="$.id",
                    variable="petId",
                )
            ],
            case_type=TestCaseType.HAPPY_PATH,
        ),
        _case(
            "createPet_happy_path",
            status=201,
            body={"name": "ab", "species": "DOG"},
            case_type=TestCaseType.HAPPY_PATH,
        ),
    ]
    semantic = [
        _case(
            "getPet_after_create",
            path="/pets/{petId}",
            method=HttpMethod.GET,
            status=200,
            path_params={"petId": "{{id}}"},
            headers={},
            dependencies=[
                Dependency(
                    source_test="createPet_happy_path",
                    source_path="$.id",
                    variable="id",
                )
            ],
            case_type=TestCaseType.HAPPY_PATH,
        )
    ]
    result = finalize_cases(contract, semantic, spec)
    assert [case.name for case in result.cases] == [
        "getPet_happy_path",
        "createPet_happy_path",
    ]
    assert "getPet_after_create" in result.dropped[0]


def test_scenario_dedupe_drops_later_of_two_identical_requests() -> None:
    first = _case("createPet_missing_name", body={"species": "DOG"})
    second = _case("createPet_missing_name_semantic", body={"species": "DOG"}, headers={})
    kept, dropped = dedupe_cases([first, second])
    assert [case.name for case in kept] == ["createPet_missing_name"]
    assert dropped == ["createPet_missing_name_semantic: duplicate of an earlier scenario"]
