from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.schemas.schemas import ApiSpec, TestCase

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_PATH_PARAM = re.compile(r"\{(\w+)\}")


@dataclass
class GenerationResult:
    cases: list[TestCase]
    contract_count: int
    semantic_count: int
    dropped: list[str] = field(default_factory=list)

    @property
    def kept_count(self) -> int:
        return len(self.cases)


def merge_cases(contract: list[TestCase], semantic: list[TestCase]) -> list[TestCase]:
    return [*contract, *semantic]


def case_fingerprint(case: TestCase) -> tuple:
    data = case.test_data.model_dump(mode="json")
    return (
        case.method.value,
        case.endpoint_path,
        case.expected_status,
        case.case_type.value,
        json.dumps(data, sort_keys=True, default=str),
    )


def dedupe_cases(cases: list[TestCase]) -> tuple[list[TestCase], list[str]]:
    seen: set[tuple] = set()
    kept: list[TestCase] = []
    dropped: list[str] = []
    for case in cases:
        key = case_fingerprint(case)
        if key in seen:
            dropped.append(f"{case.name}: duplicate of an earlier scenario")
            continue
        seen.add(key)
        kept.append(case)
    return kept, dropped


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
    merged = merge_cases(contract, semantic)
    unique, dupes = dedupe_cases(merged)
    kept, invalid = validate_cases(unique, spec)
    return GenerationResult(
        cases=kept,
        contract_count=len(contract),
        semantic_count=len(semantic),
        dropped=dupes + invalid,
    )


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
