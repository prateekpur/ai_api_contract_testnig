from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import UUID

from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions
from pydantic import TypeAdapter, ValidationError

from app.env import load_env
from app.prompts.loader import load_api_spec, load_happy_path_prompt
from app.schemas.schemas import ApiSpec, TestCase
from app.services.openapi_ingest import PROJECT_ROOT

_TEST_CASES = TypeAdapter(list[TestCase])
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def execute_happy_path_prompt(
    spec_file: str | Path,
    *,
    model: str | None = None,
) -> list[TestCase]:
    """Run the happy-path prompt against a user-supplied YAML or ingest JSON file."""
    if not spec_file:
        raise ValueError("SPEC_FILE is required")

    load_env()
    api_key = os.getenv("CURSOR_API_KEY")
    if not api_key:
        raise ValueError("CURSOR_API_KEY is required")

    _, spec = load_api_spec(spec_file)
    prompt = load_happy_path_prompt(spec_file)
    try:
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=api_key,
                model=model or os.getenv("CURSOR_MODEL", "composer-2.5"),
                local=LocalAgentOptions(cwd=str(PROJECT_ROOT)),
                tools=[],
            ),
        )
    except CursorAgentError as exc:
        raise ValueError(exc.message) from exc

    if result.status != "finished":
        raise ValueError(f"Generation failed: {result.status}")
    content = result.result
    if not content:
        raise ValueError("Model returned an empty response")
    return parse_happy_path_tests(content, spec=spec)


def parse_happy_path_tests(content: str, spec: ApiSpec | None = None) -> list[TestCase]:
    payload = _load_json(_FENCE.sub("", content.strip()))
    if isinstance(payload, dict) and "tests" in payload:
        payload = payload["tests"]
    payload = _normalize_endpoint_ids(payload, spec)
    try:
        return _TEST_CASES.validate_python(payload)
    except ValidationError as exc:
        raise ValueError(f"Model response is not a TestCase list: {exc}") from exc


def _normalize_endpoint_ids(payload: object, spec: ApiSpec | None) -> object:
    if not isinstance(payload, list):
        return payload
    by_operation = {}
    by_path_method = {}
    if spec is not None:
        by_operation = {
            endpoint.operation_id: str(endpoint.id)
            for endpoint in spec.endpoints
            if endpoint.operation_id
        }
        by_path_method = {
            (endpoint.path, endpoint.method.value): str(endpoint.id)
            for endpoint in spec.endpoints
        }
    normalized = []
    for item in payload:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        case = dict(item)
        case["endpoint_id"] = _resolve_endpoint_id(
            case.get("endpoint_id"),
            case.get("endpoint_path"),
            case.get("method"),
            by_operation,
            by_path_method,
        )
        normalized.append(case)
    return normalized


def _resolve_endpoint_id(
    value: object,
    path: object,
    method: object,
    by_operation: dict[str, str],
    by_path_method: dict[tuple[str, str], str],
) -> str | None:
    if value in (None, ""):
        if isinstance(path, str) and isinstance(method, str):
            return by_path_method.get((path, method.upper()))
        return None
    text = str(value)
    try:
        UUID(text)
        return text
    except ValueError:
        pass
    if text in by_operation:
        return by_operation[text]
    if isinstance(path, str) and isinstance(method, str):
        return by_path_method.get((path, method.upper()))
    return None


def _load_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model response is not valid JSON: {exc}") from exc
