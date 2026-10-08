from __future__ import annotations

import re

from pydantic import TypeAdapter, ValidationError

from app.schemas.schemas import (
    ApiSpec,
    Dependency,
    Determinism,
    Endpoint,
    HttpMethod,
    SchemaDefinition,
    SemanticWorkflow,
    TestCase,
    TestCaseType,
    TestData,
    TestSource,
)

_WORKFLOWS = TypeAdapter(list[SemanticWorkflow])
_OPERATION = re.compile(
    r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)$",
    re.IGNORECASE,
)
_PATH_PARAM = re.compile(r"\{(\w+)\}")


def parse_semantic_workflows(payload: object) -> list[SemanticWorkflow]:
    if isinstance(payload, dict):
        payload = payload.get("workflows") or payload.get("tests") or payload
    try:
        return _WORKFLOWS.validate_python(payload)
    except ValidationError as exc:
        raise ValueError(f"Model response is not a semantic workflow list: {exc}") from exc


def render_semantic_workflows(workflows: list[SemanticWorkflow], spec: ApiSpec) -> list[TestCase]:
    groups = _endpoint_groups(spec.endpoints)
    cases: list[TestCase] = []
    for workflow in workflows:
        for prefix, endpoints in groups.items():
            bound = _bind_workflow(workflow, endpoints)
            if bound is None:
                continue
            cases.extend(_cases_for_binding(workflow, bound, spec, prefix))
    return cases


def _endpoint_groups(endpoints: list[Endpoint]) -> dict[str, list[Endpoint]]:
    groups: dict[str, list[Endpoint]] = {}
    for endpoint in endpoints:
        prefix = _resource_prefix(endpoint.path)
        groups.setdefault(prefix, []).append(endpoint)
    return groups


def _resource_prefix(path: str) -> str:
    static = path.split("{", 1)[0].rstrip("/")
    return static or path


def _bind_workflow(
    workflow: SemanticWorkflow,
    endpoints: list[Endpoint],
) -> list[tuple[Endpoint, int, dict[str, str]]] | None:
    bound: list[tuple[Endpoint, int, dict[str, str]]] = []
    used: set[tuple[str, str]] = set()
    for step in workflow.steps:
        parsed = _parse_operation(step.operation)
        if parsed is None:
            return None
        method, generic_path = parsed
        shape = _path_shape(generic_path)
        generic_params = _PATH_PARAM.findall(generic_path)
        match = next(
            (
                endpoint
                for endpoint in endpoints
                if endpoint.method == method
                and _path_shape(endpoint.path) == shape
                and (endpoint.method.value, endpoint.path) not in used
            ),
            None,
        )
        if match is None:
            return None
        status = _status_for_step(match, step.expected_status)
        if status is None:
            return None
        spec_params = _PATH_PARAM.findall(match.path)
        param_map = dict(zip(generic_params, spec_params, strict=False))
        used.add((match.method.value, match.path))
        bound.append((match, status, param_map))
    return bound


def _parse_operation(operation: str) -> tuple[HttpMethod, str] | None:
    matched = _OPERATION.match(operation.strip())
    if matched is None:
        return None
    return HttpMethod(matched.group(1).upper()), matched.group(2)


def _path_shape(path: str) -> tuple[bool, ...]:
    parts = [part for part in path.strip("/").split("/") if part]
    return tuple(part.startswith("{") and part.endswith("}") for part in parts)


def _status_for_step(endpoint: Endpoint, expected: int | None) -> int | None:
    if expected is not None:
        return expected if str(expected) in endpoint.responses else None
    codes = sorted(int(code) for code in endpoint.responses if code.isdigit() and code.startswith("2"))
    return codes[0] if codes else None


def _cases_for_binding(
    workflow: SemanticWorkflow,
    bound: list[tuple[Endpoint, int, dict[str, str]]],
    spec: ApiSpec,
    prefix: str,
) -> list[TestCase]:
    step_names = [
        _step_name(workflow.name, endpoint, prefix, index)
        for index, (endpoint, _status, _params) in enumerate(bound)
    ]
    cases: list[TestCase] = []
    for index, ((endpoint, status, param_map), name) in enumerate(zip(bound, step_names, strict=True)):
        deps = _dependencies_for_step(workflow, index, param_map, step_names)
        path_params = _path_params(endpoint, param_map, workflow)
        body = _valid_body(endpoint.request_body, spec.schemas) if _needs_body(endpoint) else None
        schema = endpoint.responses.get(str(status))
        cases.append(
            TestCase(
                name=name,
                description=workflow.description or workflow.name,
                endpoint_id=endpoint.id,
                endpoint_path=endpoint.path,
                method=endpoint.method,
                test_data=TestData(path_params=path_params, body=body),
                dependencies=deps,
                expected_status=status,
                expected_schema=schema,
                case_type=TestCaseType.HAPPY_PATH if 200 <= status < 300 else TestCaseType.NEGATIVE,
                test_source=TestSource.SEMANTIC_INFERENCE,
                determinism=Determinism.INFERRED,
            )
        )
    return cases


def _step_name(workflow_name: str, endpoint: Endpoint, prefix: str, index: int) -> str:
    base = endpoint.operation_id or f"{endpoint.method.value.lower()}_{prefix.strip('/')}_{index}"
    return f"{base}_{workflow_name}"


def _needs_body(endpoint: Endpoint) -> bool:
    return endpoint.method in {HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH} and endpoint.request_body is not None


def _path_params(
    endpoint: Endpoint,
    param_map: dict[str, str],
    workflow: SemanticWorkflow,
) -> dict[str, str]:
    spec_to_variable = {spec_name: generic for generic, spec_name in param_map.items()}
    for dependency in workflow.dependencies:
        if dependency.variable in param_map:
            spec_to_variable[param_map[dependency.variable]] = dependency.variable
        elif dependency.variable in spec_to_variable.values():
            continue
        elif len(param_map) == 1:
            spec_to_variable[next(iter(param_map.values()))] = dependency.variable
    params: dict[str, str] = {}
    for name in _PATH_PARAM.findall(endpoint.path):
        variable = spec_to_variable.get(name, name)
        params[name] = f"{{{{{variable}}}}}"
    return params


def _dependencies_for_step(
    workflow: SemanticWorkflow,
    step_index: int,
    param_map: dict[str, str],
    step_names: list[str],
) -> list[Dependency]:
    if step_index == 0:
        return []
    deps: list[Dependency] = []
    used_variables = set(param_map) | set(param_map.values())
    for dependency in workflow.dependencies:
        if dependency.variable not in used_variables and dependency.variable not in param_map.values():
            if len(param_map) != 1 and dependency.variable not in param_map:
                continue
        source_index = dependency.source_step
        if source_index is None:
            source_index = _default_source_step(workflow, step_index)
        if source_index is None or source_index >= step_index or source_index >= len(step_names):
            continue
        deps.append(
            Dependency(
                source_test=step_names[source_index],
                source_path=dependency.source_path,
                variable=dependency.variable,
            )
        )
    return deps


def _default_source_step(workflow: SemanticWorkflow, step_index: int) -> int | None:
    for index in range(step_index):
        parsed = _parse_operation(workflow.steps[index].operation)
        if parsed and parsed[0] == HttpMethod.POST:
            return index
    return 0 if step_index else None


def _valid_body(schema: SchemaDefinition | None, schemas: dict[str, SchemaDefinition]) -> object:
    return _valid_value(_deref(schema, schemas), schemas)


def _valid_value(schema: SchemaDefinition | None, schemas: dict[str, SchemaDefinition]) -> object:
    schema = _deref(schema, schemas)
    if schema is None:
        return None
    if schema.enum:
        return schema.enum[0]
    if schema.type in {"integer", "number"}:
        minimum = schema.minimum
        return int(minimum) if minimum is not None else 1
    if schema.type == "boolean":
        return True
    if schema.type == "array":
        item = _valid_value(schema.items, schemas)
        return [] if item is None else [item]
    if schema.type == "object" or schema.properties:
        properties = schema.properties or {}
        return {key: _valid_value(properties.get(key), schemas) for key in schema.required}
    min_length = schema.min_length or 0
    if schema.format == "date-time":
        return "2026-11-01T10:00:00Z"
    if schema.format == "date":
        return "2026-11-01"
    if schema.format == "email":
        return "user@example.com"
    if schema.format == "uuid":
        return "00000000-0000-4000-8000-000000000001"
    if min_length <= 2:
        return "ab"
    return "a" * min_length


def _deref(
    schema: SchemaDefinition | None,
    schemas: dict[str, SchemaDefinition],
    seen: frozenset[str] = frozenset(),
) -> SchemaDefinition | None:
    if schema is None:
        return None
    name = (schema.ref or "").rsplit("/", 1)[-1] if schema.ref else None
    if name and name in schemas and name not in seen:
        return _deref(schemas[name], schemas, seen | {name})
    return schema
