from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.schemas.schemas import (
    ExecutionResult,
    ExecutorConfig,
    HttpMethod,
    TestCase,
    TestData,
    TestResult,
    TestStatus,
)
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


class ApiExecutor:
    """Send generated tests to an API and collect a suite ExecutionResult."""

    def __init__(
        self,
        config: ExecutorConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._client = client

    def execute(self, cases: list[TestCase]) -> ExecutionResult:
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        own_client = self._client is None
        http = self._client or httpx.Client(timeout=self.config.timeout_s)
        context: dict[str, object] = {}
        responses: dict[str, dict[str, object]] = {}
        results: list[TestResult] = []
        try:
            for case in order_cases(cases):
                result = self.execute_test_case(case, context, responses, http)
                results.append(result)
        finally:
            if own_client:
                http.close()
        counts = {status: 0 for status in TestStatus}
        for result in results:
            counts[result.status] += 1
        return ExecutionResult(
            base_url=self.config.base_url,
            started_at=started_at,
            duration_ms=(time.perf_counter() - started) * 1000,
            passed=counts[TestStatus.PASSED],
            failed=counts[TestStatus.FAILED],
            error=counts[TestStatus.ERROR],
            skipped=counts[TestStatus.SKIPPED],
            results=results,
        )

    def execute_test_case(
        self,
        case: TestCase,
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
                TestStatus.SKIPPED,
                None,
                None,
                [f'Dependency "{failed_dep}" did not run successfully'],
                started,
            )
        request: TestData | None = None
        url: str | None = None
        try:
            apply_extractions(case.dependencies, responses, context)
            request = self._prepare_request(resolve_test_data(case.test_data, context))
            url = self._url(case.endpoint_path, request)
            response = self._send(client, case.method, url, request)
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
                case,
                status,
                response.status_code,
                request,
                errors,
                started,
                body,
                schema_valid,
                url=url,
                response_headers=dict(response.headers),
            )
        except (ValueError, httpx.HTTPError) as exc:
            return _result(
                case,
                TestStatus.ERROR,
                None,
                request,
                [str(exc)],
                started,
                url=url,
            )

    def _prepare_request(self, data: TestData) -> TestData:
        headers = {**self.config.default_headers, **data.headers}
        return data.model_copy(update={"headers": headers})

    def _url(self, path: str, data: TestData) -> str:
        return self.config.base_url.rstrip("/") + interpolate_path(path, data.path_params)

    def _send(
        self,
        client: httpx.Client,
        method: HttpMethod,
        url: str,
        data: TestData,
    ) -> httpx.Response:
        kwargs: dict[str, object] = {
            "params": data.query_params or None,
            "headers": data.headers or None,
            "cookies": data.cookies or None,
        }
        if data.body is not None:
            kwargs["json"] = data.body
        return client.request(method.value, url, **kwargs)


def execute_suite(
    cases: list[TestCase],
    base_url: str,
    *,
    client: httpx.Client | None = None,
    timeout_s: float = 10,
    default_headers: dict[str, str] | None = None,
) -> ExecutionResult:
    config = ExecutorConfig(
        base_url=base_url,
        timeout_s=timeout_s,
        default_headers=default_headers or {},
    )
    return ApiExecutor(config, client=client).execute(cases)


def execute_workflow(
    cases: list[TestCase],
    base_url: str,
    *,
    client: httpx.Client | None = None,
) -> list[TestResult]:
    return execute_suite(cases, base_url, client=client).results


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
    config = ExecutorConfig(base_url=base_url)
    return ApiExecutor(config, client=client).execute_test_case(
        case, context, responses, client
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
    url: str | None = None,
    response_headers: dict[str, str] | None = None,
) -> TestResult:
    return TestResult(
        test_case_id=case.id,
        test_name=case.name,
        status=status,
        expected_status=case.expected_status,
        actual_status=actual_status,
        url=url,
        request=request,
        response_body=response_body,
        response_headers=response_headers,
        schema_valid=schema_valid,
        errors=errors,
        duration_ms=(time.perf_counter() - started) * 1000,
    )
