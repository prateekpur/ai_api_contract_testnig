from __future__ import annotations

import re
from typing import Any

from app.schemas.schemas import TestData

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def resolve_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        whole = _PLACEHOLDER.fullmatch(stripped)
        if whole:
            return lookup_variable(whole.group(1), context)
        return _PLACEHOLDER.sub(lambda match: str(lookup_variable(match.group(1), context)), value)
    if isinstance(value, dict):
        return {key: resolve_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_value(item, context) for item in value]
    return value


def resolve_test_data(test_data: TestData, context: dict[str, Any]) -> TestData:
    return TestData(
        path_params=resolve_value(test_data.path_params, context),
        query_params=resolve_value(test_data.query_params, context),
        headers=resolve_value(test_data.headers, context),
        cookies=resolve_value(test_data.cookies, context),
        body=resolve_value(test_data.body, context),
    )


def interpolate_path(path: str, path_params: dict[str, Any]) -> str:
    resolved = resolve_value(path, path_params) if "{{" in path else path
    for name, value in path_params.items():
        resolved = resolved.replace("{" + name + "}", str(value))
    return resolved


def lookup_variable(name: str, context: dict[str, Any]) -> Any:
    if name not in context:
        raise ValueError(f'Unresolved placeholder "{{{{{name}}}}}"')
    return context[name]
