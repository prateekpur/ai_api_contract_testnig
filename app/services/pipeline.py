from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.schemas.schemas import ApiSpec, Determinism, Endpoint, TestCase, TestSource
from app.services.classification import classify_generated_test
from app.services.validation import (
    ContractViolation,
    partition_violations,
    request_is_valid,
    validate_generated_test,
)

_QUALITY_DROP_CODES = (
    "success_status_invalid_request",
    "happy_path_invalid_request",
    "happy_path_error_status",
    "negative_success_status",
    "unknown_endpoint",
    "undeclared_status",
)

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_PATH_PARAM = re.compile(r"\{(\w+)\}")
_MAP_FIELDS = ("path_params", "query_params", "headers", "cookies")


@dataclass(frozen=True)
class PipelineIssue:
    """A recorded validation error or warning from the generation pipeline."""

    severity: str
    code: str
    message: str
    test_name: str
    stage: str
    path: str | None = None
    rule: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "test_name": self.test_name,
            "stage": self.stage,
            "path": self.path,
            "rule": self.rule,
        }


@dataclass
class ValidationOutcome:
    """Kept tests plus structured issues from one validation pass."""

    kept: list[TestCase]
    errors: list[PipelineIssue] = field(default_factory=list)
    warnings: list[PipelineIssue] = field(default_factory=list)

    @property
    def drop_messages(self) -> list[str]:
        return _drop_messages(self.errors)


@dataclass
class GenerationResult:
    cases: list[TestCase]
    contract_count: int
    semantic_count: int
    dropped: list[str] = field(default_factory=list)
    validation_errors: list[PipelineIssue] = field(default_factory=list)
    validation_warnings: list[PipelineIssue] = field(default_factory=list)

    @property
    def kept_count(self) -> int:
        return len(self.cases)


def merge_cases(contract: list[TestCase], semantic: list[TestCase]) -> list[TestCase]:
    return [*contract, *semantic]


def case_fingerprint(case: TestCase) -> tuple:
    return (
        case.method.value,
        case.endpoint_path,
        case.expected_status,
        case.case_type.value,
        case.mutated_field or "",
        case.constraint or "",
        _canonical_test_data(case),
    )


def dedupe_cases(cases: list[TestCase]) -> tuple[list[TestCase], list[str]]:
    kept, dropped, _aliases = _dedupe_cases(cases)
    return kept, dropped


def remap_dependencies(cases: list[TestCase], aliases: dict[str, str]) -> list[TestCase]:
    if not aliases:
        return cases
    remapped: list[TestCase] = []
    for case in cases:
        if not case.dependencies:
            remapped.append(case)
            continue
        deps = [
            dep.model_copy(update={"source_test": _resolve_alias(dep.source_test, aliases)})
            for dep in case.dependencies
        ]
        remapped.append(case.model_copy(update={"dependencies": deps}))
    return remapped


def validate_cases(cases: list[TestCase], spec: ApiSpec) -> tuple[list[TestCase], list[str]]:
    by_path_method = {(endpoint.path, endpoint.method.value): endpoint for endpoint in spec.endpoints}
    kept: list[TestCase] = []
    dropped: list[str] = []
    names: set[str] = set()
    for case in cases:
        reason = _structural_error(case, by_path_method, names)
        if reason:
            dropped.append(f"{case.name}: {reason}")
            continue
        names.add(case.name)
        kept.append(case)
    final: list[TestCase] = []
    final_names = {case.name for case in kept}
    for case in kept:
        missing = next(
            (dep.source_test for dep in case.dependencies if dep.source_test not in final_names),
            None,
        )
        if missing:
            dropped.append(f"{case.name}: unknown source_test \"{missing}\"")
            final_names.discard(case.name)
            continue
        final.append(case)
    return final, dropped


def finalize_cases(
    contract: list[TestCase],
    semantic: list[TestCase],
    spec: ApiSpec,
) -> GenerationResult:
    """Merge both tracks, then validate. Never rewrites expected status, body, or case type."""
    contract_count = len(contract)
    semantic_count = len(semantic)
    contract = [_stamp_source(case, TestSource.CONTRACT) for case in contract]
    semantic = [
        _stamp_source(case, TestSource.SEMANTIC_INFERENCE, Determinism.INFERRED)
        for case in semantic
    ]
    contract_out = apply_validation(contract, spec, stage="contract", check_dependencies=False)
    semantic_out = apply_validation(semantic, spec, stage="semantic", check_dependencies=False)
    merged = merge_cases(contract_out.kept, semantic_out.kept)
    unique, dupes, aliases = _dedupe_cases(merged)
    unique = remap_dependencies(unique, aliases)
    structural_kept, structural_dropped = validate_cases(unique, spec)
    suite_out = apply_validation(
        structural_kept, spec, stage="merged", check_dependencies=True
    )
    errors = (
        contract_out.errors
        + semantic_out.errors
        + _issues_from_dropped(structural_dropped, code="structural", stage="merged")
        + suite_out.errors
    )
    warnings = _unique_issues(contract_out.warnings + semantic_out.warnings + suite_out.warnings)
    return GenerationResult(
        cases=suite_out.kept,
        contract_count=contract_count,
        semantic_count=semantic_count,
        dropped=dupes + _drop_messages(errors),
        validation_errors=errors,
        validation_warnings=warnings,
    )


def apply_quality_gate(cases: list[TestCase], spec: ApiSpec) -> tuple[list[TestCase], list[str]]:
    outcome = apply_validation(cases, spec, stage="quality_gate", check_dependencies=True)
    return outcome.kept, outcome.drop_messages


def apply_validation(
    cases: list[TestCase],
    spec: ApiSpec,
    *,
    stage: str,
    check_dependencies: bool = True,
) -> ValidationOutcome:
    """Reject invalid tests, keep valid ones, and record errors/warnings.

    Metadata such as determinism may be stamped. expected_status, test_data,
    and case_type are left unchanged.
    """
    by_path_method = {
        (endpoint.path, endpoint.method.value): endpoint for endpoint in spec.endpoints
    }
    annotated: list[TestCase] = []
    errors: list[PipelineIssue] = []
    for case in cases:
        endpoint = by_path_method.get((case.endpoint_path, case.method.value))
        if endpoint is None:
            errors.append(
                PipelineIssue(
                    severity="error",
                    code="unknown_endpoint",
                    message=f"unknown endpoint {case.method.value} {case.endpoint_path}",
                    test_name=case.name,
                    stage=stage,
                    rule="endpoint",
                )
            )
            continue
        annotated.append(_annotate_case(case, endpoint, spec))

    candidates: list[TestCase] = []
    warnings: list[PipelineIssue] = []
    for case in annotated:
        report = validate_generated_test(case, spec)
        case_errors, case_warnings = partition_violations(report)
        if case_errors:
            errors.extend(_pipeline_issues(case_errors, severity="error", stage=stage))
            continue
        warnings.extend(_pipeline_issues(case_warnings, severity="warning", stage=stage))
        candidates.append(case)

    if not check_dependencies:
        return ValidationOutcome(kept=candidates, errors=errors, warnings=warnings)

    kept: list[TestCase] = []
    by_name = {case.name: case for case in candidates}
    for case in candidates:
        reason = _dependency_quality_error(case, by_name, spec)
        if reason:
            errors.append(
                PipelineIssue(
                    severity="error",
                    code="dependency",
                    message=reason,
                    test_name=case.name,
                    stage=stage,
                    rule="dependency",
                )
            )
            continue
        kept.append(case)
    return ValidationOutcome(kept=kept, errors=errors, warnings=warnings)


def _dedupe_cases(cases: list[TestCase]) -> tuple[list[TestCase], list[str], dict[str, str]]:
    seen: dict[tuple, str] = {}
    kept: list[TestCase] = []
    dropped: list[str] = []
    aliases: dict[str, str] = {}
    for case in cases:
        key = case_fingerprint(case)
        if key in seen:
            aliases[case.name] = seen[key]
            dropped.append(f"{case.name}: duplicate of an earlier scenario")
            continue
        seen[key] = case.name
        kept.append(case)
    return kept, dropped, aliases


def _canonical_test_data(case: TestCase) -> str:
    data = case.test_data.model_dump(mode="json")
    canonical: dict[str, object] = {}
    for field in _MAP_FIELDS:
        value = data.get(field) or {}
        if value:
            canonical[field] = _canonicalize_value(value)
    body = data.get("body")
    if body is not None:
        canonical["body"] = _canonicalize_value(body)
    return json.dumps(canonical, sort_keys=True, default=str)


def _canonicalize_value(value: object, field_key: str | None = None) -> object:
    if isinstance(value, str):
        if field_key and _PLACEHOLDER.search(value):
            return _PLACEHOLDER.sub(f"{{{{{field_key}}}}}", value)
        return value
    if isinstance(value, dict):
        return {key: _canonicalize_value(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize_value(item, field_key) for item in value]
    return value


def _resolve_alias(name: str, aliases: dict[str, str]) -> str:
    seen: set[str] = set()
    current = name
    while current in aliases and current not in seen:
        seen.add(current)
        current = aliases[current]
    return current


def _structural_error(
    case: TestCase,
    by_path_method: dict[tuple[str, str], object],
    names: set[str],
) -> str | None:
    if case.name in names:
        return "duplicate name"
    endpoint = by_path_method.get((case.endpoint_path, case.method.value))
    if endpoint is None:
        return f"unknown endpoint {case.method.value} {case.endpoint_path}"
    if str(case.expected_status) not in endpoint.responses:
        return f"status {case.expected_status} is not declared"
    for param in _PATH_PARAM.findall(case.endpoint_path):
        if param not in case.test_data.path_params:
            return f"missing path param {param}"
    declared = {dep.variable for dep in case.dependencies}
    for variable in _placeholder_names(case.test_data.model_dump()):
        if variable not in declared:
            return f"placeholder {{{{{variable}}}}} has no dependency"
    return None


def _stamp_source(
    case: TestCase,
    source: TestSource,
    determinism: Determinism | None = None,
) -> TestCase:
    updates: dict[str, object] = {"test_source": source}
    if determinism is not None:
        updates["determinism"] = determinism
    return case.model_copy(update=updates)


def _annotate_case(case: TestCase, endpoint: Endpoint, spec: ApiSpec) -> TestCase:
    valid = request_is_valid(endpoint, case.test_data, spec.schemas)
    classification = classify_generated_test(case, spec, endpoint, valid)
    schema = endpoint.responses.get(str(case.expected_status))
    return case.model_copy(
        update={
            "happy_path_contract_valid": valid,
            "determinism": classification.determinism,
            "test_source": classification.test_source,
            "classification_reason": classification.reason,
            "expected_schema_ref": _schema_ref_name(schema, spec.schemas) or case.expected_schema_ref,
        }
    )


def _pipeline_issues(
    violations: list[ContractViolation],
    *,
    severity: str,
    stage: str,
) -> list[PipelineIssue]:
    return [
        PipelineIssue(
            severity=severity,
            code=item.code,
            message=item.message,
            test_name=item.test_name or "",
            stage=stage,
            path=item.path,
            rule=item.rule,
        )
        for item in violations
    ]


def _issues_from_dropped(
    dropped: list[str],
    *,
    code: str,
    stage: str,
) -> list[PipelineIssue]:
    issues: list[PipelineIssue] = []
    for item in dropped:
        name, _, reason = item.partition(": ")
        issues.append(
            PipelineIssue(
                severity="error",
                code=code,
                message=reason or item,
                test_name=name,
                stage=stage,
            )
        )
    return issues


def _drop_messages(errors: list[PipelineIssue]) -> list[str]:
    messages: list[str] = []
    seen: set[str] = set()
    grouped: dict[str, list[PipelineIssue]] = {}
    for issue in errors:
        grouped.setdefault(issue.test_name, []).append(issue)
    for issue in errors:
        if issue.test_name in seen:
            continue
        seen.add(issue.test_name)
        messages.append(_primary_drop_message(grouped[issue.test_name]))
    return messages


def _primary_drop_message(errors: list[PipelineIssue]) -> str:
    by_code = {issue.code: issue for issue in errors}
    for code in _QUALITY_DROP_CODES:
        if code in by_code:
            issue = by_code[code]
            return f"{issue.test_name}: {issue.message}"
    first = errors[0]
    return f"{first.test_name}: {first.message}"


def _unique_issues(issues: list[PipelineIssue]) -> list[PipelineIssue]:
    unique: list[PipelineIssue] = []
    seen: set[tuple] = set()
    for issue in issues:
        key = (issue.severity, issue.code, issue.test_name, issue.path, issue.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def _dependency_quality_error(
    case: TestCase,
    by_name: dict[str, TestCase],
    spec: ApiSpec,
) -> str | None:
    for dep in case.dependencies:
        source = by_name.get(dep.source_test)
        if source is None:
            return f'unknown source_test "{dep.source_test}"'
        if not (200 <= source.expected_status < 300):
            return f'source_test "{dep.source_test}" is not a successful create'
        if not _source_path_exists(source, dep.source_path, spec):
            return f'source_path "{dep.source_path}" is not on "{dep.source_test}"'
    return None


def _source_path_exists(source: TestCase, source_path: str, spec: ApiSpec) -> bool:
    field = source_path.replace("response.body.", "").strip("$.")
    if not field or "." in field:
        return True
    schema = source.expected_schema
    if schema is None and source.expected_schema_ref:
        schema = spec.schemas.get(source.expected_schema_ref)
    if schema is None:
        return True
    if schema.properties and field in schema.properties:
        return True
    return field in schema.required


def _schema_ref_name(schema: object, schemas: dict) -> str | None:
    if schema is None:
        return None
    ref = getattr(schema, "ref", None)
    if ref:
        return str(ref).rsplit("/", 1)[-1]
    name = getattr(schema, "name", None)
    if name and name in schemas:
        return name
    items = getattr(schema, "items", None)
    if items is not None:
        item_ref = getattr(items, "ref", None)
        if item_ref:
            return str(item_ref).rsplit("/", 1)[-1]
        item_name = getattr(items, "name", None)
        if item_name and item_name in schemas:
            return item_name
    return name


def _placeholder_names(value: object) -> set[str]:
    if isinstance(value, str):
        return set(_PLACEHOLDER.findall(value))
    if isinstance(value, dict):
        names: set[str] = set()
        for item in value.values():
            names |= _placeholder_names(item)
        return names
    if isinstance(value, list):
        names = set()
        for item in value:
            names |= _placeholder_names(item)
        return names
    return set()
