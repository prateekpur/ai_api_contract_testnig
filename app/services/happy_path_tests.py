from __future__ import annotations

import json
import os
import re
from pathlib import Path

from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions
from pydantic import TypeAdapter, ValidationError

from app.env import load_env
from app.prompts.loader import load_happy_path_prompt
from app.schemas.schemas import TestCase
from app.services.openapi_ingest import PROJECT_ROOT

_TEST_CASES = TypeAdapter(list[TestCase])
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def execute_happy_path_prompt(
    spec_file: str | Path,
    *,
    model: str | None = None,
) -> list[TestCase]:
    """Run the happy-path prompt against a user-supplied ingest JSON file."""
    if not spec_file:
        raise ValueError("SPEC_FILE is required")

    load_env()
    api_key = os.getenv("CURSOR_API_KEY")
    if not api_key:
        raise ValueError("CURSOR_API_KEY is required")

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
    return parse_happy_path_tests(content)


def parse_happy_path_tests(content: str) -> list[TestCase]:
    payload = _load_json(_FENCE.sub("", content.strip()))
    if isinstance(payload, dict) and "tests" in payload:
        payload = payload["tests"]
    try:
        return _TEST_CASES.validate_python(payload)
    except ValidationError as exc:
        raise ValueError(f"Model response is not a TestCase list: {exc}") from exc


def _load_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model response is not valid JSON: {exc}") from exc
