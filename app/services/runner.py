from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from app.schemas.schemas import HttpMethod, TestCase, TestData, TestResult, TestStatus
from app.services.extraction import apply_extractions
from app.services.openapi_ingest import resolve_spec_path
from app.services.placeholders import interpolate_path, resolve_test_data
from app.services.validation import validate_response


def load_test_cases(path: str | Path) -> list[TestCase]:
    spec_path = resolve_spec_path(path)
    if not spec_path.is_file():
        raise FileNotFoundError(f"Test file not found: {spec_path}")
    payload = json.loads(spec_path.read_text())
    if isinstance(payload, dict) and "tests" in payload:
        payload = payload["tests"]
    return [TestCase.model_validate(item) for item in payload]


def execute_workflow(
    cases: list[TestCase],
    base_url: str,
    *,
    client: httpx.Client | None = None,
) -> list[TestResult]:
    own_client = client is None
    http = client or httpx.Client()
    context: dict[str, object] = {}
    responses: dict[str, dict[str, object]] = {}
    results: list[TestResult] = []
    try:
        for case in order_cases(cases):
            result = execute_test_case(case, base_url, context, responses, http)
            results.append(result)
        return results
    finally:
        if own_client:
            http.close()


def execute_scenarios(
    path: str | Path,
    base_url: str,
    *,
    client: httpx.Client | None = None,
) -> list[TestResult]:
    return execute_workflow(load_test_cases(path), base_url, client=client)


def order_cases(cases: list[TestCase]) -> list[TestCase]:
    by_name = {case.name: case for case in cases}
    visited: set[str] = set()
    visiting: set[str] = set()
    ordered: list[TestCase] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ValueError(f"Circular dependency involving {name}")
        case = by_name.get(name)
        if case is None:
            raise ValueError(f'Unknown dependency "{name}"')
        visiting.add(name)
        for dependency in case.dependencies:
            visit(dependency.source_test)
        visiting.remove(name)
        visited.add(name)
        ordered.append(case)

    for case in cases:
        visit(case.name)
    return ordered


def execute_test_case(
    case: TestCase,
    base_url: str,
    context: dict[str, object],
    responses: dict[str, dict[str, object]],
    client: httpx.Client,
) -> TestResult:
    started = time.perf_counter()
    failed_dep = next(
        (dep.source_test for dep in case.dependencies if dep.source_test not in responses),
        None,
    )
    if failed_dep:
        return _result(
            case,
            TestStatus.ERROR,
            None,
            None,
            [f'Dependency "{failed_dep}" did not run successfully'],
            started,
            schema_valid=None,
        )
    try:
        apply_extractions(case.dependencies, responses, context)
        request = resolve_test_data(case.test_data, context)
        response = _send(client, base_url, case.method, case.endpoint_path, request)
        body = _body(response)
        errors = validate_response(
            response.status_code, body, case.expected_status, case.expected_schema
        )
        schema_valid = _schema_ok(case, errors)
        if not errors:
            responses[case.name] = {"status": response.status_code, "body": body}
            status = TestStatus.PASSED
        else:
            status = TestStatus.FAILED
        return _result(
            case, status, response.status_code, request, errors, started, body, schema_valid
        )
    except (ValueError, httpx.HTTPError) as exc:
        return _result(case, TestStatus.ERROR, None, None, [str(exc)], started)


def _send(
    client: httpx.Client,
    base_url: str,
    method: HttpMethod,
    path: str,
    data: TestData,
) -> httpx.Response:
    url = base_url.rstrip("/") + interpolate_path(path, data.path_params)
    return client.request(
        method.value,
        url,
        params=data.query_params or None,
        headers=data.headers or None,
        cookies=data.cookies or None,
        json=data.body,
    )


def _schema_ok(case: TestCase, errors: list[str]) -> bool | None:
    if case.expected_schema is None or case.expected_status == 204:
        return None
    schema_errors = [error for error in errors if not error.startswith("Expected status")]
    return not schema_errors


def _body(response: httpx.Response) -> object | None:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def _result(
    case: TestCase,
    status: TestStatus,
    actual_status: int | None,
    request: TestData | None,
    errors: list[str],
    started: float,
    response_body: object = None,
    schema_valid: bool | None = None,
) -> TestResult:
    return TestResult(
        test_case_id=case.id,
        status=status,
        expected_status=case.expected_status,
        actual_status=actual_status,
        request=request,
        response_body=response_body,
        schema_valid=schema_valid,
        errors=errors,
        duration_ms=(time.perf_counter() - started) * 1000,
    )
