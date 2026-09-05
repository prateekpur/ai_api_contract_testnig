from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class ParameterLocation(str, Enum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"
    COOKIE = "cookie"


class TestCaseType(str, Enum):
    HAPPY_PATH = "happy_path"
    NEGATIVE = "negative"
    BOUNDARY = "boundary"
    SCHEMA = "schema"


class TestStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class SchemaDefinition(BaseModel):
    """JSON Schema / OpenAPI schema used for request and response bodies."""

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    title: str | None = None
    description: str | None = None
    type: str | None = None
    format: str | None = None
    properties: dict[str, SchemaDefinition] | None = None
    items: SchemaDefinition | None = None
    required: list[str] = Field(default_factory=list)
    enum: list[Any] | None = None
    example: Any = None
    default: Any = None
    nullable: bool = False
    additional_properties: bool | SchemaDefinition | None = None
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    pattern: str | None = None
    ref: str | None = Field(default=None, alias="$ref")


class Parameter(BaseModel):
    """Path, query, header, or cookie parameter on an endpoint."""

    name: str
    location: ParameterLocation
    required: bool = False
    description: str | None = None
    schema_definition: SchemaDefinition | None = None
    example: Any = None


class Endpoint(BaseModel):
    """A single HTTP operation from an API spec."""

    id: UUID = Field(default_factory=uuid4)
    path: str
    method: HttpMethod
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    parameters: list[Parameter] = Field(default_factory=list)
    request_body: SchemaDefinition | None = None
    responses: dict[str, SchemaDefinition] = Field(default_factory=dict)
    deprecated: bool = False


class ApiSpec(BaseModel):
    """Parsed API specification (typically from OpenAPI)."""

    id: UUID = Field(default_factory=uuid4)
    title: str
    version: str
    description: str | None = None
    base_url: str | None = None
    endpoints: list[Endpoint] = Field(default_factory=list)
    schemas: dict[str, SchemaDefinition] = Field(default_factory=dict)
    raw_spec: dict[str, Any] | None = None


class TestData(BaseModel):
    """Concrete request payload used to execute a test case."""

    path_params: dict[str, Any] = Field(default_factory=dict)
    query_params: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    body: Any = None


class SetupStep(BaseModel):
    """A request that creates data the main test needs."""

    name: str | None = None
    endpoint_path: str
    method: HttpMethod
    test_data: TestData
    save: dict[str, str] = Field(default_factory=dict)


class VariableExtraction(BaseModel):
    """Pull a value from a prior test response into a named variable."""

    source_test: str
    source_path: str
    variable: str


class Dependency(VariableExtraction):
    """A prior test case this test needs, with a variable taken from its response."""


class TestCase(BaseModel):
    """A generated or authored contract test for one endpoint."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str | None = None
    endpoint_id: UUID | None = None
    endpoint_path: str
    method: HttpMethod
    test_data: TestData
    setup: list[SetupStep] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    expected_status: int = 200
    expected_schema: SchemaDefinition | None = None
    case_type: TestCaseType = TestCaseType.HAPPY_PATH


class TestResult(BaseModel):
    """Outcome of executing a test case against a live or mocked API."""

    id: UUID = Field(default_factory=uuid4)
    test_case_id: UUID
    status: TestStatus
    expected_status: int
    actual_status: int | None = None
    request: TestData | None = None
    response_body: Any = None
    schema_valid: bool | None = None
    errors: list[str] = Field(default_factory=list)
    duration_ms: float | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @computed_field
    @property
    def passed(self) -> bool:
        return self.status == TestStatus.PASSED


SchemaDefinition.model_rebuild()
