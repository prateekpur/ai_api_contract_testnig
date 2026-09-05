import pytest

from app.schemas.schemas import VariableExtraction
from app.services.extraction import apply_extractions, extract_path

RESPONSE = {
    "status": 201,
    "body": {"id": 123, "name": "ab", "species": "DOG"},
}


def test_extract_response_body_id() -> None:
    assert extract_path(RESPONSE, "response.body.id") == 123


def test_extract_response_body_name() -> None:
    assert extract_path(RESPONSE, "response.body.name") == "ab"


def test_extract_response_status() -> None:
    assert extract_path(RESPONSE, "response.status") == 201


def test_extract_json_path_id() -> None:
    assert extract_path(RESPONSE, "$.id") == 123


def test_apply_extractions_writes_context() -> None:
    context: dict[str, object] = {}
    apply_extractions(
        [
            VariableExtraction(
                source_test="createPet_happy_path",
                source_path="response.body.id",
                variable="petId",
            )
        ],
        {"createPet_happy_path": RESPONSE},
        context,
    )
    assert context == {"petId": 123}


def test_apply_extractions_missing_source_raises() -> None:
    with pytest.raises(ValueError, match="No response stored"):
        apply_extractions(
            [
                VariableExtraction(
                    source_test="createPet_happy_path",
                    source_path="$.id",
                    variable="petId",
                )
            ],
            {},
            {},
        )
