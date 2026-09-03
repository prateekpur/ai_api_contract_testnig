from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.schemas.schemas import (
    ApiSpec,
    Endpoint,
    HttpMethod,
    Parameter,
    ParameterLocation,
    SchemaDefinition,
)

HTTP_METHODS = {method.value.lower(): method for method in HttpMethod}
PATH_ITEM_NON_METHOD_KEYS = {"$ref", "summary", "description", "servers", "parameters"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC_PATH = PROJECT_ROOT / "sample_specs" / "petstore.yaml"


def resolve_spec_path(path: str | Path) -> Path:
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("Spec path must be inside the project directory") from exc
    return resolved


def ingest_openapi_file(path: str | Path) -> ApiSpec:
    spec_path = resolve_spec_path(path)
    if not spec_path.is_file():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")
    return ingest_openapi_bytes(spec_path.read_bytes())


def ingest_openapi_bytes(content: bytes) -> ApiSpec:
    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    return parse_openapi(raw)


def parse_openapi(raw: Any) -> ApiSpec:
    if not isinstance(raw, dict):
        raise ValueError("OpenAPI spec must be a YAML/JSON object")
    if not raw.get("openapi") and not raw.get("swagger"):
        raise ValueError("File is not an OpenAPI or Swagger specification")

    info = raw.get("info") or {}
    title = info.get("title")
    version = info.get("version")
    if not title or not version:
        raise ValueError("OpenAPI spec is missing info.title or info.version")

    return ApiSpec(
        title=title,
        version=str(version),
        description=info.get("description"),
        base_url=_base_url(raw),
        endpoints=_discover_endpoints(raw),
        schemas=_component_schemas(raw),
        raw_spec=raw,
    )


def _base_url(raw: dict[str, Any]) -> str | None:
    servers = raw.get("servers") or []
    if servers and isinstance(servers[0], dict):
        return servers[0].get("url")

    host = raw.get("host")
    if not host:
        return None
    schemes = raw.get("schemes") or ["https"]
    base_path = raw.get("basePath") or ""
    return f"{schemes[0]}://{host}{base_path}"


def _component_schemas(raw: dict[str, Any]) -> dict[str, SchemaDefinition]:
    components = raw.get("components") or {}
    schemas = components.get("schemas") or raw.get("definitions") or {}
    if not isinstance(schemas, dict):
        return {}
    return {name: to_schema(schema, name=name) for name, schema in schemas.items()}


def _discover_endpoints(raw: dict[str, Any]) -> list[Endpoint]:
    paths = raw.get("paths") or {}
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI spec paths must be an object")

    endpoints: list[Endpoint] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_params = _parameters(path_item.get("parameters"))
        for key, operation in path_item.items():
            if key in PATH_ITEM_NON_METHOD_KEYS or not isinstance(operation, dict):
                continue
            method = HTTP_METHODS.get(key.lower())
            if method is None:
                continue
            endpoints.append(
                Endpoint(
                    path=path,
                    method=method,
                    operation_id=operation.get("operationId"),
                    summary=operation.get("summary"),
                    description=operation.get("description"),
                    tags=list(operation.get("tags") or []),
                    parameters=_merge_parameters(path_params, _parameters(operation.get("parameters"))),
                    request_body=_request_body_schema(operation.get("requestBody")),
                    responses=_response_schemas(operation.get("responses")),
                    deprecated=bool(operation.get("deprecated", False)),
                )
            )
    return endpoints


def _parameters(raw_params: Any) -> list[Parameter]:
    if not isinstance(raw_params, list):
        return []
    parameters: list[Parameter] = []
    for raw in raw_params:
        if not isinstance(raw, dict) or "name" not in raw:
            continue
        location = raw.get("in", "query")
        try:
            param_location = ParameterLocation(location)
        except ValueError:
            continue
        schema = raw.get("schema")
        if schema is None and raw.get("type"):
            schema = {key: raw[key] for key in ("type", "format", "enum", "minimum", "maximum") if key in raw}
        parameters.append(
            Parameter(
                name=raw["name"],
                location=param_location,
                required=bool(raw.get("required", location == "path")),
                description=raw.get("description"),
                schema_definition=to_schema(schema) if schema else None,
                example=raw.get("example"),
            )
        )
    return parameters


def _merge_parameters(path_params: list[Parameter], operation_params: list[Parameter]) -> list[Parameter]:
    merged = {(param.name, param.location): param for param in path_params}
    for param in operation_params:
        merged[(param.name, param.location)] = param
    return list(merged.values())


def _request_body_schema(request_body: Any) -> SchemaDefinition | None:
    if not isinstance(request_body, dict):
        return None
    return _schema_from_content(request_body.get("content"))


def _response_schemas(responses: Any) -> dict[str, SchemaDefinition]:
    if not isinstance(responses, dict):
        return {}
    discovered: dict[str, SchemaDefinition] = {}
    for status, response in responses.items():
        if not isinstance(response, dict):
            continue
        schema = _schema_from_content(response.get("content"))
        if schema is None and response.get("schema"):
            schema = to_schema(response.get("schema"))
        if schema is None:
            schema = SchemaDefinition(description=response.get("description"))
        elif not schema.description:
            schema.description = response.get("description")
        discovered[str(status)] = schema
    return discovered


def _schema_from_content(content: Any) -> SchemaDefinition | None:
    if not isinstance(content, dict) or not content:
        return None
    media = content.get("application/json") or next(iter(content.values()))
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    return to_schema(schema) if schema else None


def to_schema(raw: Any, name: str | None = None) -> SchemaDefinition:
    if not isinstance(raw, dict):
        return SchemaDefinition(name=name)

    additional = raw.get("additionalProperties")
    additional_properties: bool | SchemaDefinition | None
    if isinstance(additional, dict):
        additional_properties = to_schema(additional)
    elif isinstance(additional, bool):
        additional_properties = additional
    else:
        additional_properties = None

    properties = None
    raw_properties = raw.get("properties")
    if isinstance(raw_properties, dict):
        properties = {key: to_schema(value, name=key) for key, value in raw_properties.items()}

    items = raw.get("items")
    return SchemaDefinition(
        name=name,
        title=raw.get("title"),
        description=raw.get("description"),
        type=raw.get("type"),
        format=raw.get("format"),
        properties=properties,
        items=to_schema(items) if isinstance(items, dict) else None,
        required=list(raw.get("required") or []),
        enum=raw.get("enum"),
        example=raw.get("example"),
        default=raw.get("default"),
        nullable=bool(raw.get("nullable", False)),
        additional_properties=additional_properties,
        minimum=raw.get("minimum"),
        maximum=raw.get("maximum"),
        min_length=raw.get("minLength"),
        max_length=raw.get("maxLength"),
        min_items=raw.get("minItems"),
        max_items=raw.get("maxItems"),
        pattern=raw.get("pattern"),
        ref=raw.get("$ref"),
    )
