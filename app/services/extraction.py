from __future__ import annotations

from typing import Any

from app.schemas.schemas import VariableExtraction


def extract_path(response: dict[str, Any], source_path: str) -> Any:
    """Read a value from a stored test response.

    Supports ``$.id``, ``response.body.id``, ``response.body.name``,
    and ``response.status``.
    """
    path = source_path.strip()
    if path.startswith("response."):
        path = path[len("response.") :]
    if path in {"status", "$.status"}:
        return response["status"]
    if path in {"body", "$"}:
        return response.get("body")
    if path.startswith("body."):
        return _walk(response.get("body"), path[5:].split("."))
    if path.startswith("$."):
        return _walk(response.get("body"), path[2:].split("."))
    return _walk(response.get("body"), path.split("."))


def apply_extractions(
    dependencies: list[VariableExtraction],
    responses: dict[str, dict[str, Any]],
    context: dict[str, Any],
) -> None:
    for dependency in dependencies:
        source = responses.get(dependency.source_test)
        if source is None:
            raise ValueError(f'No response stored for "{dependency.source_test}"')
        context[dependency.variable] = extract_path(source, dependency.source_path)


def _walk(current: Any, parts: list[str]) -> Any:
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f'Could not extract "{".".join(parts)}" from response body')
        current = current[part]
    return current
