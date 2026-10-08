from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.schemas import (
    Dependency,
    ExecutionResult,
    ExecutorConfig,
    HttpMethod,
    SchemaDefinition,
    TestCase,
    TestData,
    TestStatus,
)
from app.services.runner import ApiExecutor, execute_workflow, order_cases


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
    assert results[0].url == "http://localhost:8000/pets"
    assert results[0].request is not None
    assert results[0].request.body == {"name": "ab", "species": "DOG"}
    assert results[0].response_body == {"id": 123, "name": "ab", "species": "DOG"}
    assert results[1].url == "http://localhost:8000/pets/123"
    assert results[1].test_name == "getPet_happy_path"


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
    assert results[1].status == TestStatus.SKIPPED
    assert "createPet_happy_path" in results[1].errors[0]
    assert results[1].url is None
    assert seen == ["POST /pets"]


def list_pets() -> TestCase:
    return TestCase(
        name="getPets_happy_path",
        endpoint_path="/pets",
        method=HttpMethod.GET,
        test_data=TestData(),
        expected_status=200,
    )


def test_execute_returns_suite_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 1}])
        return httpx.Response(201, json={"id": 123, "name": "ab", "species": "DOG"})

    suite = ApiExecutor(
        ExecutorConfig(base_url="http://localhost:8000"),
        client=_client(handler),
    ).execute([list_pets(), create_pet()])
    assert suite.passed == 2
    assert suite.failed == 0
    assert suite.error == 0
    assert suite.skipped == 0
    assert suite.base_url == "http://localhost:8000"
    assert len(suite.results) == 2
    assert {result.test_name for result in suite.results} == {
        "getPets_happy_path",
        "createPet_happy_path",
    }
    assert all(result.url and result.request is not None for result in suite.results)
    assert all(result.actual_status is not None for result in suite.results)
    assert all(result.response_headers is not None for result in suite.results)


def test_default_headers_applied_and_overridden() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["X-Api-Key"] = request.headers["X-Api-Key"]
        seen["X-Case"] = request.headers["X-Case"]
        return httpx.Response(200, json=[{"id": 1}])

    case = TestCase(
        name="getPets_happy_path",
        endpoint_path="/pets",
        method=HttpMethod.GET,
        test_data=TestData(headers={"X-Case": "yes"}),
        expected_status=200,
    )
    ApiExecutor(
        ExecutorConfig(
            base_url="http://localhost:8000",
            default_headers={"X-Api-Key": "secret", "X-Case": "no"},
        ),
        client=_client(handler),
    ).execute([case])
    assert seen == {"X-Api-Key": "secret", "X-Case": "yes"}


def test_timeout_config_used(monkeypatch) -> None:
    created: dict[str, object] = {}
    responses = [httpx.Response(200, json=[])]

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            created.update(kwargs)

        def request(self, *args, **kwargs) -> httpx.Response:
            return responses[0]

        def close(self) -> None:
            return None

    monkeypatch.setattr("app.services.runner.httpx.Client", FakeClient)
    ApiExecutor(ExecutorConfig(base_url="http://localhost:8000", timeout_s=3.5)).execute(
        [list_pets()]
    )
    assert created["timeout"] == 3.5


def test_get_does_not_send_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.content in (b"", None)
        return httpx.Response(200, json=[])

    result = execute_workflow([list_pets()], "http://localhost:8000", client=_client(handler))
    assert result[0].status == TestStatus.PASSED


def test_timeout_error_is_recorded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    results = execute_workflow([list_pets()], "http://localhost:8000", client=_client(handler))
    assert results[0].status == TestStatus.ERROR
    assert "timed out" in results[0].errors[0]
    assert results[0].request is not None
    assert results[0].url == "http://localhost:8000/pets"


def test_run_endpoint_returns_execution_result(monkeypatch) -> None:
    case = list_pets()

    def fake_execute(self, cases):
        return ExecutionResult(
            base_url=self.config.base_url,
            started_at=datetime.now(timezone.utc),
            duration_ms=1.5,
            passed=1,
            failed=0,
            error=0,
            skipped=0,
            results=[
                {
                    "test_case_id": cases[0].id,
                    "test_name": cases[0].name,
                    "status": TestStatus.PASSED,
                    "expected_status": 200,
                    "actual_status": 200,
                    "url": "http://localhost:8000/pets",
                }
            ],
        )

    monkeypatch.setattr("app.routers.tests.ApiExecutor.execute", fake_execute)
    client = TestClient(app)
    response = client.post(
        "/tests/run",
        json={
            "base_url": "http://localhost:8000",
            "cases": [case.model_dump(mode="json")],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["passed"] == 1
    assert body["failed"] == 0
    assert body["results"][0]["test_name"] == "getPets_happy_path"
    assert body["results"][0]["url"] == "http://localhost:8000/pets"


def test_run_endpoint_requires_cases_or_path() -> None:
    client = TestClient(app)
    response = client.post("/tests/run", json={"base_url": "http://localhost:8000"})
    assert response.status_code == 400
    assert response.json()["detail"] == "cases or path is required"
