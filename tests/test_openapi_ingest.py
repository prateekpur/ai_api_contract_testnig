from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.routers import specs as specs_router
from app.schemas.schemas import HttpMethod, ParameterLocation
from app.services.openapi_ingest import DEFAULT_SPEC_PATH, ingest_openapi_file

SAMPLE_SPEC = Path(__file__).resolve().parents[1] / "sample_specs" / "petstore.yaml"


def test_ingest_petstore_discovers_api_details() -> None:
    spec = ingest_openapi_file(SAMPLE_SPEC)

    assert spec.title == "Simple Pet Store API"
    assert spec.version == "1.0.0"
    assert spec.base_url == "http://localhost:8000"
    assert set(spec.schemas) == {"CreatePetRequest", "Pet"}
    assert spec.schemas["CreatePetRequest"].required == ["name", "species"]
    assert spec.schemas["Pet"].properties is not None
    assert spec.schemas["Pet"].properties["id"].type == "integer"

    discovered = {(endpoint.method, endpoint.path) for endpoint in spec.endpoints}
    assert discovered == {
        (HttpMethod.GET, "/pets"),
        (HttpMethod.POST, "/pets"),
        (HttpMethod.GET, "/pets/{petId}"),
        (HttpMethod.DELETE, "/pets/{petId}"),
    }

    create_pet = next(endpoint for endpoint in spec.endpoints if endpoint.operation_id == "createPet")
    assert create_pet.request_body is not None
    assert create_pet.request_body.ref == "#/components/schemas/CreatePetRequest"
    assert set(create_pet.responses) == {"201", "400"}

    get_pet = next(endpoint for endpoint in spec.endpoints if endpoint.operation_id == "getPet")
    assert len(get_pet.parameters) == 1
    assert get_pet.parameters[0].name == "petId"
    assert get_pet.parameters[0].location == ParameterLocation.PATH
    assert get_pet.parameters[0].required is True
    assert get_pet.parameters[0].schema_definition is not None
    assert get_pet.parameters[0].schema_definition.minimum == 1


def test_ingest_endpoint_uses_sample_yaml() -> None:
    specs_router._specs.clear()
    client = TestClient(app)

    response = client.post("/specs/ingest", params={"path": "sample_specs/petstore.yaml"})

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Simple Pet Store API"
    assert len(body["endpoints"]) == 4
    assert DEFAULT_SPEC_PATH.name == "petstore.yaml"

    spec_id = body["id"]
    discovered = client.get(f"/specs/{spec_id}/endpoints")
    assert discovered.status_code == 200
    assert {item["operation_id"] for item in discovered.json()} == {
        "getPets",
        "createPet",
        "getPet",
        "deletePet",
    }
