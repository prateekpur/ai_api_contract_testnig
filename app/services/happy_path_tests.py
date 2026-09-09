from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import UUID

from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions
from pydantic import TypeAdapter, ValidationError

from app.env import load_env
from app.prompts.loader import load_api_spec, load_contract_prompt, load_semantic_prompt
from app.schemas.schemas import ApiSpec, SchemaDefinition, TestCase
from app.services.openapi_ingest import PROJECT_ROOT
from app.services.pipeline import GenerationResult, finalize_cases

_TEST_CASES = TypeAdapter(list[TestCase])
_FENCE_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)


def generate_all_tests(
    spec_file: str | Path,
    *,
    model: str | None = None,
) -> list[TestCase]:
    """Run contract and semantic prompts, then merge, dedupe, and validate."""
    return generate_pipeline(spec_file, model=model).cases


def generate_pipeline(
    spec_file: str | Path,
    *,
    model: str | None = None,
) -> GenerationResult:
    """Run both generation tracks and return cases plus drop counts."""
    if not spec_file:
        raise ValueError("SPEC_FILE is required")
    _, spec = load_api_spec(spec_file)
    contract = _execute_prompt(spec_file, load_contract_prompt, model=model, label="contract-tests")
    semantic = _execute_prompt(spec_file, load_semantic_prompt, model=model, label="semantic-tests")
    return finalize_cases(contract, semantic, spec)


def _execute_prompt(
    spec_file: str | Path,
    load_prompt,
    *,
    model: str | None = None,
    label: str = "prompt",
) -> list[TestCase]:
    if not spec_file:
        raise ValueError("SPEC_FILE is required")

    load_env()
    api_key = os.getenv("CURSOR_API_KEY")
    if not api_key:
        raise ValueError("CURSOR_API_KEY is required")

    _, spec = load_api_spec(spec_file)
    prompt = load_prompt(spec_file)
    last_error: ValueError | None = None
    for _attempt in range(2):
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
            raise ValueError(f"{label}: {exc.message}") from exc

        if result.status != "finished":
            last_error = ValueError(f"{label}: Generation failed: {result.status}")
            continue
        content = _model_text(result.result)
        if not content:
            last_error = ValueError(f"{label}: Model returned an empty response")
            continue
        try:
            return parse_happy_path_tests(content, spec=spec)
        except ValueError as exc:
            last_error = ValueError(f"{label}: {exc}")
    raise last_error or ValueError(f"{label}: Generation failed")


def parse_happy_path_tests(content: str, spec: ApiSpec | None = None) -> list[TestCase]:
    payload = _load_json(_extract_json_text(content))
    if isinstance(payload, dict) and "tests" in payload:
        payload = payload["tests"]
    payload = _normalize_endpoint_ids(payload, spec)
    try:
        cases = _TEST_CASES.validate_python(payload)
    except ValidationError as exc:
        raise ValueError(f"Model response is not a TestCase list: {exc}") from exc
    if spec is None:
        return cases
    return [_expand_case_refs(case, spec.schemas) for case in cases]


def _model_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _extract_json_text(content: str) -> str:
    text = content.strip()
    fenced = _FENCE_BLOCK.search(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text:
        raise ValueError("Model response is not valid JSON: empty response")
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    start_array = text.find("[")
    start_object = text.find("{")
    starts = [index for index in (start_array, start_object) if index >= 0]
    if not starts:
        preview = text[:240].replace("\n", " ")
        raise ValueError(f"Model response is not valid JSON: {preview}")
    try:
        payload, _ = json.JSONDecoder().raw_decode(text[min(starts) :])
    except json.JSONDecodeError as exc:
        preview = text[:240].replace("\n", " ")
        raise ValueError(f"Model response is not valid JSON: {exc}; {preview}") from exc
    return json.dumps(payload)


def _expand_case_refs(case: TestCase, schemas: dict[str, SchemaDefinition]) -> TestCase:
    return case.model_copy(update={"expected_schema": _expand_schema(case.expected_schema, schemas)})


def _expand_schema(
    schema: SchemaDefinition | None,
    schemas: dict[str, SchemaDefinition],
    seen: frozenset[str] = frozenset(),
) -> SchemaDefinition | None:
    if schema is None:
        return None
    name = _ref_name(schema.ref)
    if name and name in schemas and name not in seen:
        resolved = _expand_schema(schemas[name], schemas, seen | {name})
        pointer_only = schema.type is None and schema.items is None and not schema.properties
        if pointer_only:
            if resolved is None:
                return schema.model_copy(update={"ref": None})
            return resolved.model_copy(update={"ref": None, "name": resolved.name or name})
    properties = None
    if schema.properties:
        properties = {
            key: _expand_schema(value, schemas, seen) for key, value in schema.properties.items()
        }
    additional = schema.additional_properties
    if isinstance(additional, SchemaDefinition):
        additional = _expand_schema(additional, schemas, seen)
    return schema.model_copy(
        update={
            "ref": None,
            "properties": properties,
            "items": _expand_schema(schema.items, schemas, seen),
            "additional_properties": additional,
        }
    )


def _ref_name(ref: str | None) -> str | None:
    if not ref:
        return None
    return ref.rsplit("/", 1)[-1]


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
        preview = text[:240].replace("\n", " ") if text else "<empty>"
        raise ValueError(f"Model response is not valid JSON: {exc}; {preview}") from exc
