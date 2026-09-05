from __future__ import annotations

from typing import Any

from app.schemas.schemas import SchemaDefinition


def validate_response(
    status: int,
    body: Any,
    expected_status: int,
    schema: SchemaDefinition | None,
) -> list[str]:
    errors: list[str] = []
    if status != expected_status:
        errors.append(f"Expected status {expected_status}, got {status}")
    if expected_status == 204:
        return errors
    if schema is not None:
        errors.extend(_validate_value(body, schema, "$"))
    return errors


def _validate_value(value: Any, schema: SchemaDefinition, path: str) -> list[str]:
    if value is None:
        if schema.nullable:
            return []
        return [f"{path} is required"]

    errors: list[str] = []
    expected_type = schema.type
    if expected_type == "object":
        if not isinstance(value, dict):
            return [f"{path} should be an object"]
        for field in schema.required:
            if field not in value:
                errors.append(f"{path}.{field} is required")
        properties = schema.properties or {}
        for field, child in properties.items():
            if field in value:
                errors.extend(_validate_value(value[field], child, f"{path}.{field}"))
    elif expected_type == "array":
        if not isinstance(value, list):
            return [f"{path} should be an array"]
        if schema.items is not None:
            for index, item in enumerate(value):
                errors.extend(_validate_value(item, schema.items, f"{path}[{index}]"))
    elif expected_type == "string":
        if not isinstance(value, str):
            errors.append(f"{path} should be a string")
    elif expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{path} should be an integer")
    elif expected_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"{path} should be a number")
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path} should be a boolean")

    if schema.enum is not None and value not in schema.enum:
        errors.append(f"{path} should be one of {schema.enum}")
    return errors
