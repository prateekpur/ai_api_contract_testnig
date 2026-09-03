from uuid import uuid4

import pytest

from app.prompts.loader import load_happy_path_prompt
from app.schemas.schemas import HttpMethod, TestCaseType
from app.services.happy_path_tests import parse_happy_path_tests


def test_load_happy_path_prompt_uses_user_file() -> None:
    prompt = load_happy_path_prompt("fixtures/petstore.ingest.json")
    assert "Simple Pet Store API" in prompt
    assert "{{SPEC_JSON}}" not in prompt
    assert "fixtures/petstore.ingest.json" in prompt


def test_parse_happy_path_tests_from_json_array() -> None:
    endpoint_id = uuid4()
    cases = parse_happy_path_tests(
        f"""
        [
          {{
            "name": "getPets_happy_path",
            "description": "List pets",
            "endpoint_id": "{endpoint_id}",
            "endpoint_path": "/pets",
            "method": "GET",
            "case_type": "happy_path",
            "expected_status": 200,
            "test_data": {{
              "path_params": {{}},
              "query_params": {{}},
              "headers": {{}},
              "cookies": {{}},
              "body": null
            }}
          }}
        ]
        """
    )
    assert len(cases) == 1
    assert cases[0].name == "getPets_happy_path"
    assert cases[0].method == HttpMethod.GET
    assert cases[0].case_type == TestCaseType.HAPPY_PATH
    assert cases[0].expected_status == 200


def test_execute_happy_path_prompt_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    with pytest.raises(ValueError, match="CURSOR_API_KEY is required"):
        from app.services.happy_path_tests import execute_happy_path_prompt

        execute_happy_path_prompt("fixtures/petstore.ingest.json")


def test_parse_happy_path_tests_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_happy_path_tests("not-json")
