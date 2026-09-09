from uuid import uuid4

import pytest

from app.prompts.loader import load_api_spec, load_contract_prompt, load_semantic_prompt
from app.schemas.schemas import HttpMethod, TestCaseType
from app.services.happy_path_tests import generate_all_tests, parse_happy_path_tests


def test_load_contract_prompt_uses_user_file() -> None:
    prompt = load_contract_prompt("fixtures/petstore.ingest.json")
    assert "Simple Pet Store API" in prompt
    assert "{{SPEC_JSON}}" not in prompt
    assert "fixtures/petstore.ingest.json" in prompt
    assert "happy path" in prompt.lower()
    assert "required field" in prompt.lower()
    assert "enum field" in prompt.lower()


def test_parse_happy_path_tests_includes_dependencies() -> None:
    cases = parse_happy_path_tests(
        """
        [
          {
            "name": "getPet_happy_path",
            "description": "Get a pet after creating it",
            "endpoint_path": "/pets/{petId}",
            "method": "GET",
            "case_type": "happy_path",
            "expected_status": 200,
            "dependencies": [
              {
                "source_test": "createPet_happy_path",
                "source_path": "$.id",
                "variable": "petId"
              }
            ],
            "test_data": {
              "path_params": {"petId": "{{petId}}"},
              "body": null
            }
          }
        ]
        """
    )
    assert cases[0].dependencies[0].source_test == "createPet_happy_path"
    assert cases[0].dependencies[0].source_path == "$.id"
    assert cases[0].dependencies[0].variable == "petId"
    assert cases[0].test_data.path_params["petId"] == "{{petId}}"


def test_parse_happy_path_tests_includes_setup_data() -> None:
    cases = parse_happy_path_tests(
        """
        [
          {
            "name": "getPet_happy_path",
            "description": "Get a pet after creating it",
            "endpoint_path": "/pets/{petId}",
            "method": "GET",
            "case_type": "happy_path",
            "expected_status": 200,
            "setup": [
              {
                "name": "createPet",
                "endpoint_path": "/pets",
                "method": "POST",
                "test_data": {
                  "body": {"name": "ab", "species": "DOG"}
                },
                "save": {"petId": "id"}
              }
            ],
            "test_data": {
              "path_params": {"petId": "{{petId}}"},
              "body": null
            }
          }
        ]
        """
    )
    assert cases[0].setup[0].endpoint_path == "/pets"
    assert cases[0].setup[0].save == {"petId": "id"}
    assert cases[0].test_data.path_params["petId"] == "{{petId}}"


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


def test_generate_all_tests_requires_api_key(monkeypatch) -> None:
    monkeypatch.setattr("app.services.happy_path_tests.load_env", lambda: None)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    with pytest.raises(ValueError, match="CURSOR_API_KEY is required"):
        generate_all_tests("fixtures/petstore.ingest.json")


def test_load_contract_prompt_accepts_yaml() -> None:
    prompt = load_contract_prompt("sample_specs/petstore.yaml")
    assert "Simple Pet Store API" in prompt
    assert "getPets" in prompt


def test_load_semantic_prompt_uses_user_file() -> None:
    prompt = load_semantic_prompt("fixtures/petstore.ingest.json")
    assert "semantic" in prompt.lower()
    assert "workflow" in prompt.lower()
    assert "Simple Pet Store API" in prompt
    assert "{{SPEC_JSON}}" not in prompt


def test_parse_expands_schema_refs() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    cases = parse_happy_path_tests(
        """
        [
          {
            "name": "getPets_happy_path",
            "endpoint_path": "/pets",
            "method": "GET",
            "case_type": "happy_path",
            "expected_status": 200,
            "expected_schema": {
              "type": "array",
              "items": {"ref": "#/components/schemas/Pet"}
            },
            "test_data": {"path_params": {}, "query_params": {}, "headers": {}, "cookies": {}, "body": null}
          },
          {
            "name": "createPet_happy_path",
            "endpoint_path": "/pets",
            "method": "POST",
            "case_type": "happy_path",
            "expected_status": 201,
            "expected_schema": {"$ref": "#/components/schemas/Pet"},
            "test_data": {"body": {"name": "ab", "species": "DOG"}}
          }
        ]
        """,
        spec=spec,
    )
    items = cases[0].expected_schema.items
    assert cases[0].expected_schema.ref is None
    assert items is not None
    assert items.ref is None
    assert items.type == "object"
    assert items.required == ["id", "name", "species"]
    assert items.properties["species"].enum == ["DOG", "CAT", "BIRD"]
    pet = cases[1].expected_schema
    assert pet is not None
    assert pet.ref is None
    assert pet.type == "object"
    assert pet.required == ["id", "name", "species"]


def test_parse_maps_operation_id_to_endpoint_uuid() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    get_pets = next(endpoint for endpoint in spec.endpoints if endpoint.operation_id == "getPets")
    cases = parse_happy_path_tests(
        """
        [
          {
            "name": "getPets_happy_path",
            "description": "List pets",
            "endpoint_id": "getPets",
            "endpoint_path": "/pets",
            "method": "GET",
            "case_type": "happy_path",
            "expected_status": 200,
            "test_data": {"path_params": {}, "query_params": {}, "headers": {}, "cookies": {}, "body": null}
          }
        ]
        """,
        spec=spec,
    )
    assert cases[0].endpoint_id == get_pets.id


def test_parse_drops_invalid_endpoint_id_without_spec() -> None:
    cases = parse_happy_path_tests(
        """
        [
          {
            "name": "getPets_happy_path",
            "description": "List pets",
            "endpoint_id": "getPets",
            "endpoint_path": "/pets",
            "method": "GET",
            "case_type": "happy_path",
            "expected_status": 200,
            "test_data": {"path_params": {}, "query_params": {}, "headers": {}, "cookies": {}, "body": null}
          }
        ]
        """
    )
    assert cases[0].endpoint_id is None


def test_parse_happy_path_tests_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_happy_path_tests("not-json")


def test_parse_happy_path_tests_accepts_fenced_json() -> None:
    cases = parse_happy_path_tests(
        """
        Here are the tests:
        ```json
        [
          {
            "name": "getPets_happy_path",
            "endpoint_path": "/pets",
            "method": "GET",
            "case_type": "happy_path",
            "expected_status": 200,
            "test_data": {"path_params": {}, "query_params": {}, "headers": {}, "cookies": {}, "body": null}
          }
        ]
        ```
        """
    )
    assert cases[0].name == "getPets_happy_path"


def test_parse_happy_path_tests_accepts_json_after_prose() -> None:
    cases = parse_happy_path_tests(
        """
        Generated cases:
        [
          {
            "name": "getPets_happy_path",
            "endpoint_path": "/pets",
            "method": "GET",
            "case_type": "happy_path",
            "expected_status": 200,
            "test_data": {"path_params": {}, "query_params": {}, "headers": {}, "cookies": {}, "body": null}
          }
        ]
        """
    )
    assert cases[0].name == "getPets_happy_path"
