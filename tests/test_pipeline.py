from app.prompts.loader import load_api_spec
from app.schemas.schemas import Dependency, HttpMethod, TestCase, TestCaseType, TestData
from app.services.happy_path_tests import generate_all_tests
from app.services.pipeline import dedupe_cases, finalize_cases, validate_cases


def _case(
    name: str,
    *,
    path: str = "/pets",
    method: HttpMethod = HttpMethod.POST,
    status: int = 400,
    body: object | None = None,
    path_params: dict | None = None,
    query_params: dict | None = None,
    dependencies: list[Dependency] | None = None,
    case_type: TestCaseType = TestCaseType.NEGATIVE,
) -> TestCase:
    data = {"path_params": path_params or {}, "body": body}
    if query_params is not None:
        data["query_params"] = query_params
    return TestCase(
        name=name,
        endpoint_path=path,
        method=method,
        test_data=TestData(**data),
        expected_status=status,
        case_type=case_type,
        dependencies=dependencies or [],
    )


def test_dedupe_keeps_first_identical_missing_name() -> None:
    first = _case("createPet_missing_name", body={"species": "DOG"})
    second = _case("createPet_missing_name_again", body={"species": "DOG"})
    kept, dropped = dedupe_cases([first, second])
    assert [case.name for case in kept] == ["createPet_missing_name"]
    assert "createPet_missing_name_again" in dropped[0]


def test_finalize_keeps_semantic_create_then_get() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    contract = [
        _case(
            "createPet_happy_path",
            status=201,
            body={"name": "ab", "species": "DOG"},
            case_type=TestCaseType.HAPPY_PATH,
        )
    ]
    semantic = [
        _case(
            "createPet_for_get",
            status=201,
            body={"name": "cd", "species": "CAT"},
            case_type=TestCaseType.HAPPY_PATH,
        ),
        _case(
            "getPet_after_create",
            path="/pets/{petId}",
            method=HttpMethod.GET,
            status=200,
            path_params={"petId": "{{petId}}"},
            dependencies=[
                Dependency(
                    source_test="createPet_for_get",
                    source_path="$.id",
                    variable="petId",
                )
            ],
            case_type=TestCaseType.HAPPY_PATH,
        ),
    ]
    result = finalize_cases(contract, semantic, spec)
    assert [case.name for case in result.cases] == [
        "createPet_happy_path",
        "createPet_for_get",
        "getPet_after_create",
    ]
    assert result.contract_count == 1
    assert result.semantic_count == 2
    assert result.dropped == []


def test_validate_drops_placeholder_without_dependency() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    cases = [
        _case(
            "getPet_orphan",
            path="/pets/{petId}",
            method=HttpMethod.GET,
            status=200,
            path_params={"petId": "{{petId}}"},
            case_type=TestCaseType.HAPPY_PATH,
        )
    ]
    kept, dropped = validate_cases(cases, spec)
    assert kept == []
    assert "getPet_orphan" in dropped[0]
    assert "petId" in dropped[0]


def test_generate_all_tests_runs_both_tracks(monkeypatch) -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    contract = [
        _case("createPet_missing_name", body={"species": "DOG"}),
    ]
    semantic = [
        _case("createPet_missing_name_dup", body={"species": "DOG"}),
        _case(
            "createPet_for_get",
            status=201,
            body={"name": "ab", "species": "DOG"},
            case_type=TestCaseType.HAPPY_PATH,
        ),
        _case(
            "getPet_after_create",
            path="/pets/{petId}",
            method=HttpMethod.GET,
            status=200,
            path_params={"petId": "{{petId}}"},
            dependencies=[
                Dependency(
                    source_test="createPet_for_get",
                    source_path="$.id",
                    variable="petId",
                )
            ],
            case_type=TestCaseType.HAPPY_PATH,
        ),
    ]

    def fake_execute(spec_file, load_prompt, *, model=None, label="prompt"):
        return contract if label == "contract-tests" else semantic

    monkeypatch.setattr("app.services.happy_path_tests._execute_prompt", fake_execute)
    cases = generate_all_tests("sample_specs/petstore.yaml")
    assert [case.name for case in cases] == [
        "createPet_missing_name",
        "createPet_for_get",
        "getPet_after_create",
    ]
    assert spec.title == "Simple Pet Store API"


def test_dedupe_collapses_renamed_placeholders() -> None:
    first = _case(
        "getPet_happy_path",
        path="/pets/{petId}",
        method=HttpMethod.GET,
        status=200,
        path_params={"petId": "{{petId}}"},
        case_type=TestCaseType.HAPPY_PATH,
    )
    second = _case(
        "getPet_after_create",
        path="/pets/{petId}",
        method=HttpMethod.GET,
        status=200,
        path_params={"petId": "{{id}}"},
        case_type=TestCaseType.HAPPY_PATH,
    )
    kept, dropped = dedupe_cases([first, second])
    assert [case.name for case in kept] == ["getPet_happy_path"]
    assert "getPet_after_create" in dropped[0]


def test_dedupe_collapses_empty_and_omitted_query_params() -> None:
    first = _case("createPet_missing_name", body={"species": "DOG"})
    second = _case(
        "createPet_missing_name_again",
        body={"species": "DOG"},
        query_params={},
    )
    kept, dropped = dedupe_cases([first, second])
    assert [case.name for case in kept] == ["createPet_missing_name"]
    assert dropped


def test_finalize_rewrites_source_test_when_create_is_duplicate() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    contract = [
        _case(
            "createPet_happy_path",
            status=201,
            body={"name": "ab", "species": "DOG"},
            case_type=TestCaseType.HAPPY_PATH,
        )
    ]
    semantic = [
        _case(
            "createPet_for_get",
            status=201,
            body={"name": "ab", "species": "DOG"},
            case_type=TestCaseType.HAPPY_PATH,
        ),
        _case(
            "getPet_after_create",
            path="/pets/{petId}",
            method=HttpMethod.GET,
            status=200,
            path_params={"petId": "{{petId}}"},
            dependencies=[
                Dependency(
                    source_test="createPet_for_get",
                    source_path="$.id",
                    variable="petId",
                )
            ],
            case_type=TestCaseType.HAPPY_PATH,
        ),
    ]
    result = finalize_cases(contract, semantic, spec)
    assert [case.name for case in result.cases] == [
        "createPet_happy_path",
        "getPet_after_create",
    ]
    assert result.cases[1].dependencies[0].source_test == "createPet_happy_path"
    assert "createPet_for_get" in result.dropped[0]
