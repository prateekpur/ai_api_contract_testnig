import httpx

from app.schemas.schemas import (
    Dependency,
    HttpMethod,
    SchemaDefinition,
    TestCase,
    TestData,
    TestStatus,
)
from app.services.runner import execute_workflow, order_cases


def pet_schema() -> SchemaDefinition:
    return SchemaDefinition(
        type="object",
        required=["id", "name", "species"],
        properties={
            "id": SchemaDefinition(type="integer"),
            "name": SchemaDefinition(type="string"),
            "species": SchemaDefinition(type="string", enum=["DOG", "CAT", "BIRD"]),
        },
    )


def create_pet() -> TestCase:
    return TestCase(
        name="createPet_happy_path",
        endpoint_path="/pets",
        method=HttpMethod.POST,
        test_data=TestData(body={"name": "ab", "species": "DOG"}),
        expected_status=201,
        expected_schema=pet_schema(),
    )


def get_pet() -> TestCase:
    return TestCase(
        name="getPet_happy_path",
        endpoint_path="/pets/{petId}",
        method=HttpMethod.GET,
        test_data=TestData(path_params={"petId": "{{petId}}"}),
        dependencies=[
            Dependency(
                source_test="createPet_happy_path",
                source_path="response.body.id",
                variable="petId",
            )
        ],
        expected_status=200,
        expected_schema=pet_schema(),
    )


def delete_pet() -> TestCase:
    return TestCase(
        name="deletePet_happy_path",
        endpoint_path="/pets/{petId}",
        method=HttpMethod.DELETE,
        test_data=TestData(path_params={"petId": "{{petId}}"}),
        dependencies=[
            Dependency(
                source_test="createPet_happy_path",
                source_path="$.id",
                variable="petId",
            )
        ],
        expected_status=204,
    )


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_order_cases_runs_create_first() -> None:
    create, get = create_pet(), get_pet()
    ordered = order_cases([get, create])
    assert [case.name for case in ordered] == ["createPet_happy_path", "getPet_happy_path"]


def test_create_then_get_uses_captured_id() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/pets":
            return httpx.Response(201, json={"id": 123, "name": "ab", "species": "DOG"})
        if request.method == "GET" and request.url.path == "/pets/123":
            return httpx.Response(200, json={"id": 123, "name": "ab", "species": "DOG"})
        return httpx.Response(404, json={"detail": "not found"})

    results = execute_workflow(
        [get_pet(), create_pet()],
        "http://localhost:8000",
        client=_client(handler),
    )
    assert [result.status for result in results] == [TestStatus.PASSED, TestStatus.PASSED]
    assert seen == ["POST /pets", "GET /pets/123"]
    assert results[1].schema_valid is True
    assert results[1].actual_status == 200


def test_create_then_delete_uses_captured_id() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/pets":
            return httpx.Response(201, json={"id": 123, "name": "ab", "species": "DOG"})
        if request.method == "DELETE" and request.url.path == "/pets/123":
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "not found"})

    results = execute_workflow(
        [delete_pet(), create_pet()],
        "http://localhost:8000",
        client=_client(handler),
    )
    assert [result.status for result in results] == [TestStatus.PASSED, TestStatus.PASSED]
    assert seen == ["POST /pets", "DELETE /pets/123"]


def test_dependent_skipped_when_create_fails() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(400, json={"detail": "invalid"})
        raise AssertionError("dependent request should not be sent")

    results = execute_workflow(
        [get_pet(), create_pet()],
        "http://localhost:8000",
        client=_client(handler),
    )
    assert results[0].status == TestStatus.FAILED
    assert results[0].actual_status == 400
    assert results[1].status == TestStatus.ERROR
    assert "createPet_happy_path" in results[1].errors[0]
    assert seen == ["POST /pets"]
