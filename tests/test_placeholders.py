import pytest

from app.schemas.schemas import TestData
from app.services.placeholders import interpolate_path, lookup_variable, resolve_test_data, resolve_value


def test_lookup_variable_returns_context_value() -> None:
    assert lookup_variable("petId", {"petId": 123}) == 123


def test_lookup_variable_missing_raises() -> None:
    with pytest.raises(ValueError, match=r'Unresolved placeholder "\{\{petId\}\}"'):
        lookup_variable("petId", {})


def test_resolve_placeholder_to_path() -> None:
    params = resolve_value({"petId": "{{petId}}"}, {"petId": 123})
    assert params == {"petId": 123}
    assert interpolate_path("/pets/{petId}", params) == "/pets/123"


def test_resolve_embedded_placeholder_stays_string() -> None:
    assert resolve_value("pet-{{petId}}", {"petId": 123}) == "pet-123"


def test_resolve_test_data_walks_nested_values() -> None:
    data = resolve_test_data(
        TestData(
            path_params={"petId": "{{petId}}"},
            body={"ownerId": "{{petId}}", "name": "ab"},
        ),
        {"petId": 123},
    )
    assert data.path_params["petId"] == 123
    assert data.body == {"ownerId": 123, "name": "ab"}
