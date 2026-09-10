from app.prompts.loader import load_api_spec
from app.schemas.schemas import HttpMethod, TestCaseType
from app.services.happy_path_tests import parse_semantic_tests
from app.services.semantic_workflows import parse_semantic_workflows, render_semantic_workflows

CREATE_THEN_RETRIEVE = """
[
  {
    "name": "create_resource_then_retrieve",
    "case_type": "semantic",
    "steps": [
      { "operation": "POST /resources" },
      { "operation": "GET /resources/{resourceId}" }
    ],
    "dependencies": [
      {
        "type": "DATA_DEPENDENCY",
        "source_path": "$.id",
        "variable": "resourceId"
      }
    ]
  }
]
"""

CREATE_THEN_DELETE_THEN_GET = """
[
  {
    "name": "create_resource_then_delete_then_retrieve",
    "case_type": "semantic",
    "steps": [
      { "operation": "POST /resources" },
      { "operation": "DELETE /resources/{resourceId}" },
      { "operation": "GET /resources/{resourceId}", "expected_status": 404 }
    ],
    "dependencies": [
      {
        "type": "DATA_DEPENDENCY",
        "source_path": "$.id",
        "variable": "resourceId"
      }
    ]
  }
]
"""


def test_parse_semantic_workflows_from_json_array() -> None:
    workflows = parse_semantic_workflows(
        [
            {
                "name": "create_resource_then_retrieve",
                "case_type": "semantic",
                "steps": [
                    {"operation": "POST /resources"},
                    {"operation": "GET /resources/{resourceId}"},
                ],
                "dependencies": [
                    {
                        "type": "DATA_DEPENDENCY",
                        "source_path": "$.id",
                        "variable": "resourceId",
                    }
                ],
            }
        ]
    )
    assert workflows[0].name == "create_resource_then_retrieve"
    assert workflows[0].case_type == "semantic"
    assert workflows[0].steps[0].operation == "POST /resources"
    assert workflows[0].dependencies[0].variable == "resourceId"


def test_render_create_then_retrieve_uses_petstore_names() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    cases = render_semantic_workflows(parse_semantic_workflows(
        [
            {
                "name": "create_resource_then_retrieve",
                "case_type": "semantic",
                "steps": [
                    {"operation": "POST /resources"},
                    {"operation": "GET /resources/{resourceId}"},
                ],
                "dependencies": [
                    {
                        "type": "DATA_DEPENDENCY",
                        "source_path": "$.id",
                        "variable": "resourceId",
                    }
                ],
            }
        ]
    ), spec)
    assert [case.name for case in cases] == [
        "createPet_create_resource_then_retrieve",
        "getPet_create_resource_then_retrieve",
    ]
    create, retrieve = cases
    assert create.method == HttpMethod.POST
    assert create.endpoint_path == "/pets"
    assert create.expected_status == 201
    assert create.test_data.body == {"name": "ab", "species": "DOG"}
    assert create.case_type == TestCaseType.HAPPY_PATH
    assert retrieve.method == HttpMethod.GET
    assert retrieve.endpoint_path == "/pets/{petId}"
    assert retrieve.test_data.path_params == {"petId": "{{resourceId}}"}
    assert retrieve.dependencies[0].source_test == "createPet_create_resource_then_retrieve"
    assert retrieve.dependencies[0].source_path == "$.id"
    assert retrieve.dependencies[0].variable == "resourceId"


def test_render_skips_undeclared_error_status() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    workflows = parse_semantic_workflows(
        [
            {
                "name": "create_resource_conflict",
                "case_type": "semantic",
                "steps": [
                    {"operation": "POST /resources"},
                    {"operation": "POST /resources", "expected_status": 409},
                ],
                "dependencies": [],
            }
        ]
    )
    assert render_semantic_workflows(workflows, spec) == []


def test_parse_semantic_tests_renders_petstore_workflow() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    cases = parse_semantic_tests(CREATE_THEN_RETRIEVE, spec=spec)
    assert cases[0].name == "createPet_create_resource_then_retrieve"
    assert cases[1].expected_schema is not None
    assert cases[1].expected_schema.ref is None
    assert cases[1].expected_schema.required == ["id", "name", "species"]


def test_render_404_after_delete_on_petstore() -> None:
    _, spec = load_api_spec("sample_specs/petstore.yaml")
    cases = parse_semantic_tests(CREATE_THEN_DELETE_THEN_GET, spec=spec)
    assert [case.name for case in cases] == [
        "createPet_create_resource_then_delete_then_retrieve",
        "deletePet_create_resource_then_delete_then_retrieve",
        "getPet_create_resource_then_delete_then_retrieve",
    ]
    assert cases[2].expected_status == 404
    assert cases[2].case_type == TestCaseType.NEGATIVE
    assert cases[2].dependencies[0].source_test == (
        "createPet_create_resource_then_delete_then_retrieve"
    )
