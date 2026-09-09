from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.schemas.schemas import ApiSpec, TestCase

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_PATH_PARAM = re.compile(r"\{(\w+)\}")
_MAP_FIELDS = ("path_params", "query_params", "headers", "cookies")


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
    return (
        case.method.value,
        case.endpoint_path,
        case.expected_status,
        case.case_type.value,
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
    merged = merge_cases(contract, semantic)
    unique, dupes, aliases = _dedupe_cases(merged)
    unique = remap_dependencies(unique, aliases)
    kept, invalid = validate_cases(unique, spec)
    return GenerationResult(
        cases=kept,
        contract_count=len(contract),
        semantic_count=len(semantic),
        dropped=dupes + invalid,
    )


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
