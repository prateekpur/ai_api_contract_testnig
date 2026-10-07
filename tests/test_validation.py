from app.schemas.schemas import HttpMethod, SchemaDefinition, TestData
from app.prompts.loader import load_api_spec
from app.services.validation import validate_request, validate_response


def pet_schema() -> SchemaDefinition:
    return SchemaDefinition(
        type="object",
        required=["id", "name", "species"],
        properties={
            "id": SchemaDefinition(type="integer"),
            "name": SchemaDefinition(type="string"),
            "species": SchemaDefinition(type="string", enum=["DOG", "CAT", "BIRD"]),
        },
    )


def test_valid_pet_object() -> None:
    errors = validate_response(
        200,
        {"id": 123, "name": "ab", "species": "DOG"},
        200,
        pet_schema(),
    )
    assert errors == []


def test_wrong_status() -> None:
    errors = validate_response(404, {"detail": "missing"}, 200, pet_schema())
    assert any("Expected status 200, got 404" in error for error in errors)


def test_body_must_be_object() -> None:
    errors = validate_response(200, ["not", "an", "object"], 200, pet_schema())
    assert "$ should be an object" in errors


def test_required_fields() -> None:
    errors = validate_response(200, {"id": 123}, 200, pet_schema())
    assert "$.name is required" in errors
    assert "$.species is required" in errors


def test_type_mismatch() -> None:
    errors = validate_response(
        200,
        {"id": "123", "name": "ab", "species": "DOG"},
        200,
        pet_schema(),
    )
    assert "$.id should be an integer" in errors


def test_species_enum() -> None:
    errors = validate_response(
        200,
        {"id": 123, "name": "ab", "species": "FISH"},
        200,
        pet_schema(),
    )
    assert any("should be one of" in error and "DOG" in error for error in errors)


def test_request_rejects_invalid_date_time() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    endpoint = next(
        item for item in spec.endpoints if item.path == "/events" and item.method == HttpMethod.POST
    )
    errors = validate_request(
        endpoint,
        TestData(
            body={
                "name": "Summit",
                "category": "CONFERENCE",
                "startDate": "ab",
                "capacity": 10,
            }
        ),
        spec.schemas,
    )
    assert any("date-time" in error for error in errors)


def test_request_accepts_empty_update_object() -> None:
    _, spec = load_api_spec("sample_specs/arbitary_api.yaml")
    endpoint = next(
        item
        for item in spec.endpoints
        if item.path == "/events/{eventId}" and item.method == HttpMethod.PUT
    )
    errors = validate_request(
        endpoint,
        TestData(path_params={"eventId": "{{eventId}}"}, body={}),
        spec.schemas,
    )
    assert errors == []
