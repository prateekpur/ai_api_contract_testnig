from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.schemas.schemas import (
    ApiSpec,
    Endpoint,
    ParameterLocation,
    SchemaDefinition,
    TestCase,
    TestCaseType,
    TestData,
)

_PLACEHOLDER = re.compile(r"^\{\{\w+\}\}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PATH_PARAM = re.compile(r"\{(\w+)\}")

REJECT_CODES = frozenset(
    {
        "unknown_endpoint",
        "undeclared_status",
        "happy_path_invalid_request",
        "success_status_invalid_request",
        "happy_path_error_status",
        "negative_success_status",
    }
)
WARNING_CODES = frozenset({"boundary_mismatch"})

__all__ = [
    "ContractViolation",
    "REJECT_CODES",
    "SuiteValidationReport",
    "TestValidationReport",
    "WARNING_CODES",
    "check_request",
    "partition_violations",
    "request_is_valid",
    "validate_generated_suite",
    "validate_generated_test",
    "validate_request",
    "validate_response",
]


@dataclass(frozen=True)
class ContractViolation:
    """One structured problem found while checking a generated test against OpenAPI."""

    code: str
    message: str
    path: str | None = None
    rule: str | None = None
    test_name: str | None = None

    def with_test(self, name: str) -> ContractViolation:
        return ContractViolation(
            code=self.code,
            message=self.message,
            path=self.path,
            rule=self.rule,
            test_name=name,
        )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "rule": self.rule,
            "test_name": self.test_name,
        }


@dataclass
class TestValidationReport:
    """Result of validating one generated TestCase against a spec."""

    test_name: str
    request_valid: bool
    violations: list[ContractViolation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def messages(self) -> list[str]:
        return [item.message for item in self.violations]


@dataclass
class SuiteValidationReport:
    """Result of validating a generated suite against a spec."""

    reports: list[TestValidationReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(report.ok for report in self.reports)

    @property
    def violations(self) -> list[ContractViolation]:
        found: list[ContractViolation] = []
        for report in self.reports:
            found.extend(report.violations)
        return found


def validate_generated_suite(
    cases: list[TestCase],
    spec: ApiSpec,
) -> SuiteValidationReport:
    """Validate every generated test against the OpenAPI spec."""
    return SuiteValidationReport(
        reports=[validate_generated_test(case, spec) for case in cases]
    )


def partition_violations(
    report: TestValidationReport,
) -> tuple[list[ContractViolation], list[ContractViolation]]:
    """Split findings into rejectable errors and keep-with-warning flags.

    Request-schema issues on negative or boundary 4xx tests are expected and omitted.
    When a test is rejected, those same request issues are kept as error detail.
    """
    errors = [item for item in report.violations if item.code in REJECT_CODES]
    warnings = [item for item in report.violations if item.code in WARNING_CODES]
    if errors:
        errors.extend(
            item
            for item in report.violations
            if item.code not in REJECT_CODES and item.code not in WARNING_CODES
        )
    return errors, warnings


def validate_generated_test(case: TestCase, spec: ApiSpec) -> TestValidationReport:
    """Validate one generated test: request data, status, constraints, classification."""
    endpoint = _endpoint(spec, case.endpoint_path, case.method.value)
    violations: list[ContractViolation] = []
    if endpoint is None:
        violations.append(
            ContractViolation(
                code="unknown_endpoint",
                message=f"unknown endpoint {case.method.value} {case.endpoint_path}",
                rule="endpoint",
                test_name=case.name,
            )
        )
        return TestValidationReport(case.name, request_valid=False, violations=violations)

    if str(case.expected_status) not in endpoint.responses:
        violations.append(
            ContractViolation(
                code="undeclared_status",
                message=f"status {case.expected_status} is not declared",
                rule="response_status",
                test_name=case.name,
            )
        )

    request_issues = check_request(endpoint, case.test_data, spec.schemas)
    request_valid = not request_issues
    violations.extend(item.with_test(case.name) for item in request_issues)
    violations.extend(_classification_issues(case, request_valid))
    violations.extend(_boundary_issues(case, endpoint, spec.schemas))
    return TestValidationReport(
        test_name=case.name,
        request_valid=request_valid,
        violations=violations,
    )


def check_request(
    endpoint: Endpoint,
    test_data: TestData,
    schemas: dict[str, SchemaDefinition],
) -> list[ContractViolation]:
    """Check request body and parameters against the operation contract."""
    issues: list[ContractViolation] = []
    body_schema = _deref(endpoint.request_body, schemas)
    if body_schema is not None:
        if test_data.body is None:
            issues.append(
                ContractViolation(
                    code="required",
                    message="request body is required",
                    path="$",
                    rule="required",
                )
            )
        else:
            issues.extend(_check_value(test_data.body, body_schema, "$", schemas))
    issues.extend(_check_parameters(endpoint, test_data, schemas))
    return issues


def validate_response(
    status: int,
    body: Any,
    expected_status: int,
    schema: SchemaDefinition | None,
) -> list[str]:
    errors: list[str] = []
    if status != expected_status:
        errors.append(f"Expected status {expected_status}, got {status}")
    if expected_status == 204:
        return errors
    if schema is not None:
        errors.extend(item.message for item in _check_value(body, schema, "$", {}))
    return errors


def validate_request(
    endpoint: Endpoint,
    test_data: TestData,
    schemas: dict[str, SchemaDefinition],
) -> list[str]:
    return [item.message for item in check_request(endpoint, test_data, schemas)]


def request_is_valid(
    endpoint: Endpoint,
    test_data: TestData,
    schemas: dict[str, SchemaDefinition],
) -> bool:
    return not check_request(endpoint, test_data, schemas)


def _classification_issues(case: TestCase, request_valid: bool) -> list[ContractViolation]:
    issues: list[ContractViolation] = []
    success = 200 <= case.expected_status < 300
    if success and not request_valid:
        issues.append(
            ContractViolation(
                code="success_status_invalid_request",
                message="request violates the contract but expects success",
                rule="classification",
                test_name=case.name,
            )
        )
    if case.case_type == TestCaseType.HAPPY_PATH and not request_valid:
        issues.append(
            ContractViolation(
                code="happy_path_invalid_request",
                message="happy-path test has request data that violates the contract",
                rule="classification",
                test_name=case.name,
            )
        )
    if case.case_type == TestCaseType.HAPPY_PATH and not success:
        issues.append(
            ContractViolation(
                code="happy_path_error_status",
                message="happy-path test expects a non-success status",
                rule="classification",
                test_name=case.name,
            )
        )
    if (
        case.case_type == TestCaseType.NEGATIVE
        and success
        and request_valid
    ):
        issues.append(
            ContractViolation(
                code="negative_success_status",
                message="negative test has a valid request and expects success",
                rule="classification",
                test_name=case.name,
            )
        )
    return issues


def _boundary_issues(
    case: TestCase,
    endpoint: Endpoint,
    schemas: dict[str, SchemaDefinition],
) -> list[ContractViolation]:
    if not case.constraint or not case.mutated_field:
        return []
    value = _lookup_field(case.test_data, case.mutated_field)
    if value is None or _is_placeholder(value):
        return []
    schema = _field_schema(endpoint, case.mutated_field, schemas)
    if schema is None:
        return []
    expected = _expected_boundary_value(schema, case.constraint)
    if expected is None:
        return []
    actual = _actual_boundary_value(value, case.constraint)
    if actual != expected:
        return [
            ContractViolation(
                code="boundary_mismatch",
                message=(
                    f"{case.mutated_field} value {value!r} does not match "
                    f"constraint {case.constraint} (expected {expected!r})"
                ),
                path=case.mutated_field,
                rule="boundary",
                test_name=case.name,
            )
        ]
    return []


def _actual_boundary_value(value: Any, constraint: str) -> Any:
    key = constraint.upper().replace(" ", "")
    if "LENGTH" in key:
        return len(value) if isinstance(value, str) else None
    return value


def _expected_boundary_value(schema: SchemaDefinition, constraint: str) -> Any:
    key = constraint.upper().replace(" ", "")
    if schema.minimum is not None:
        minimum = int(schema.minimum) if schema.type == "integer" else schema.minimum
        mapping = {
            "MIN-1": minimum - 1,
            "MIN": minimum,
            "MIN+1": minimum + 1,
        }
        if key in mapping:
            return mapping[key]
    if schema.maximum is not None:
        maximum = int(schema.maximum) if schema.type == "integer" else schema.maximum
        mapping = {
            "MAX-1": maximum - 1,
            "MAX": maximum,
            "MAX+1": maximum + 1,
        }
        if key in mapping:
            return mapping[key]
    if schema.min_length is not None:
        mapping = {
            "MIN_LENGTH-1": schema.min_length - 1,
            "MIN_LENGTH": schema.min_length,
            "MIN_LENGTH+1": schema.min_length + 1,
        }
        if key in mapping:
            return mapping[key]
    if schema.max_length is not None:
        mapping = {
            "MAX_LENGTH-1": schema.max_length - 1,
            "MAX_LENGTH": schema.max_length,
            "MAX_LENGTH+1": schema.max_length + 1,
        }
        if key in mapping:
            return mapping[key]
    return None


def _lookup_field(data: TestData, name: str) -> Any:
    if isinstance(data.body, dict) and name in data.body:
        return data.body[name]
    for bag in (data.path_params, data.query_params, data.headers, data.cookies):
        if name in bag:
            return bag[name]
    return None


def _field_schema(
    endpoint: Endpoint,
    name: str,
    schemas: dict[str, SchemaDefinition],
) -> SchemaDefinition | None:
    body = _deref(endpoint.request_body, schemas)
    if body and body.properties and name in body.properties:
        return _deref(body.properties[name], schemas)
    for param in endpoint.parameters:
        if param.name == name:
            return _deref(param.schema_definition, schemas)
    return None


def _check_parameters(
    endpoint: Endpoint,
    test_data: TestData,
    schemas: dict[str, SchemaDefinition],
) -> list[ContractViolation]:
    bags = {
        ParameterLocation.PATH: test_data.path_params,
        ParameterLocation.QUERY: test_data.query_params,
        ParameterLocation.HEADER: test_data.headers,
        ParameterLocation.COOKIE: test_data.cookies,
    }
    issues: list[ContractViolation] = []
    for param in endpoint.parameters:
        values = bags.get(param.location, {})
        path = f"{param.location.value}.{param.name}"
        if param.name not in values:
            if param.required:
                issues.append(
                    ContractViolation(
                        code="required",
                        message=f"{path} is required",
                        path=path,
                        rule="required",
                    )
                )
            continue
        schema = _deref(param.schema_definition, schemas)
        if schema is not None:
            issues.extend(_check_value(values[param.name], schema, path, schemas))
    declared = {param.name for param in endpoint.parameters if param.location == ParameterLocation.PATH}
    for name in _PATH_PARAM.findall(endpoint.path):
        if name not in declared and name not in test_data.path_params:
            issues.append(
                ContractViolation(
                    code="required",
                    message=f"path.{name} is required",
                    path=f"path.{name}",
                    rule="required",
                )
            )
    return issues


def _check_value(
    value: Any,
    schema: SchemaDefinition,
    path: str,
    schemas: dict[str, SchemaDefinition],
) -> list[ContractViolation]:
    schema = _deref(schema, schemas) or schema
    if _is_placeholder(value):
        return []
    if value is None:
        if schema.nullable:
            return []
        return [
            ContractViolation(
                code="required",
                message=f"{path} is required",
                path=path,
                rule="required",
            )
        ]

    issues: list[ContractViolation] = []
    expected_type = schema.type
    if expected_type == "object":
        if not isinstance(value, dict):
            return [
                ContractViolation(
                    code="type",
                    message=f"{path} should be an object",
                    path=path,
                    rule="type",
                )
            ]
        for required_name in schema.required:
            if required_name not in value:
                issues.append(
                    ContractViolation(
                        code="required",
                        message=f"{path}.{required_name} is required",
                        path=f"{path}.{required_name}",
                        rule="required",
                    )
                )
        properties = schema.properties or {}
        for prop_name, child in properties.items():
            if prop_name in value:
                issues.extend(
                    _check_value(value[prop_name], child, f"{path}.{prop_name}", schemas)
                )
    elif expected_type == "array":
        if not isinstance(value, list):
            return [
                ContractViolation(
                    code="type",
                    message=f"{path} should be an array",
                    path=path,
                    rule="type",
                )
            ]
        if schema.min_items is not None and len(value) < schema.min_items:
            issues.append(
                ContractViolation(
                    code="minItems",
                    message=f"{path} should have at least {schema.min_items} items",
                    path=path,
                    rule="minItems",
                )
            )
        if schema.max_items is not None and len(value) > schema.max_items:
            issues.append(
                ContractViolation(
                    code="maxItems",
                    message=f"{path} should have at most {schema.max_items} items",
                    path=path,
                    rule="maxItems",
                )
            )
        if schema.items is not None:
            for index, item in enumerate(value):
                issues.extend(_check_value(item, schema.items, f"{path}[{index}]", schemas))
    elif expected_type == "string":
        if not isinstance(value, str):
            issues.append(
                ContractViolation(
                    code="type",
                    message=f"{path} should be a string",
                    path=path,
                    rule="type",
                )
            )
        else:
            issues.extend(_string_constraints(value, schema, path))
    elif expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            issues.append(
                ContractViolation(
                    code="type",
                    message=f"{path} should be an integer",
                    path=path,
                    rule="type",
                )
            )
        else:
            issues.extend(_numeric_constraints(value, schema, path))
    elif expected_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            issues.append(
                ContractViolation(
                    code="type",
                    message=f"{path} should be a number",
                    path=path,
                    rule="type",
                )
            )
        else:
            issues.extend(_numeric_constraints(value, schema, path))
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            issues.append(
                ContractViolation(
                    code="type",
                    message=f"{path} should be a boolean",
                    path=path,
                    rule="type",
                )
            )

    if schema.enum is not None and value not in schema.enum:
        issues.append(
            ContractViolation(
                code="enum",
                message=f"{path} should be one of {schema.enum}",
                path=path,
                rule="enum",
            )
        )
    return issues


def _string_constraints(value: str, schema: SchemaDefinition, path: str) -> list[ContractViolation]:
    issues: list[ContractViolation] = []
    if schema.min_length is not None and len(value) < schema.min_length:
        issues.append(
            ContractViolation(
                code="minLength",
                message=f"{path} should have minLength {schema.min_length}",
                path=path,
                rule="minLength",
            )
        )
    if schema.max_length is not None and len(value) > schema.max_length:
        issues.append(
            ContractViolation(
                code="maxLength",
                message=f"{path} should have maxLength {schema.max_length}",
                path=path,
                rule="maxLength",
            )
        )
    if schema.pattern:
        try:
            if re.search(schema.pattern, value) is None:
                issues.append(
                    ContractViolation(
                        code="pattern",
                        message=f"{path} should match pattern {schema.pattern}",
                        path=path,
                        rule="pattern",
                    )
                )
        except re.error:
            pass
    fmt = schema.format
    if fmt == "date" and _DATE.fullmatch(value) is None:
        issues.append(
            ContractViolation(
                code="format",
                message=f"{path} should be format date",
                path=path,
                rule="format",
            )
        )
    elif fmt == "date-time" and _DATE_TIME.fullmatch(value) is None:
        issues.append(
            ContractViolation(
                code="format",
                message=f"{path} should be format date-time",
                path=path,
                rule="format",
            )
        )
    elif fmt == "email" and _EMAIL.fullmatch(value) is None:
        issues.append(
            ContractViolation(
                code="format",
                message=f"{path} should be format email",
                path=path,
                rule="format",
            )
        )
    elif fmt == "uuid":
        try:
            UUID(value)
        except ValueError:
            issues.append(
                ContractViolation(
                    code="format",
                    message=f"{path} should be format uuid",
                    path=path,
                    rule="format",
                )
            )
    return issues


def _numeric_constraints(
    value: int | float, schema: SchemaDefinition, path: str
) -> list[ContractViolation]:
    issues: list[ContractViolation] = []
    if schema.minimum is not None and value < schema.minimum:
        issues.append(
            ContractViolation(
                code="minimum",
                message=f"{path} should be >= {schema.minimum}",
                path=path,
                rule="minimum",
            )
        )
    if schema.maximum is not None and value > schema.maximum:
        issues.append(
            ContractViolation(
                code="maximum",
                message=f"{path} should be <= {schema.maximum}",
                path=path,
                rule="maximum",
            )
        )
    return issues


def _endpoint(spec: ApiSpec, path: str, method: str) -> Endpoint | None:
    method = method.upper()
    return next(
        (
            endpoint
            for endpoint in spec.endpoints
            if endpoint.path == path and endpoint.method.value == method
        ),
        None,
    )


def _is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and _PLACEHOLDER.fullmatch(value) is not None


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
