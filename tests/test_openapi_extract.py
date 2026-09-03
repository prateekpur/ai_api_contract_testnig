from pathlib import Path

import pytest

from app.schemas.schemas import ApiSpec, Endpoint, HttpMethod, ParameterLocation
from app.services.openapi_ingest import ingest_openapi_file

INSURANCE_SPEC = Path(__file__).resolve().parents[1] / "sample_specs" / "insurance_api.yaml"
PETSTORE_SPEC = Path(__file__).resolve().parents[1] / "sample_specs" / "petstore.yaml"


@pytest.fixture(scope="module")
def insurance_spec() -> ApiSpec:
    return ingest_openapi_file(INSURANCE_SPEC)


@pytest.fixture(scope="module")
def petstore_spec() -> ApiSpec:
    return ingest_openapi_file(PETSTORE_SPEC)


def _endpoint(spec: ApiSpec, operation_id: str) -> Endpoint:
    return next(endpoint for endpoint in spec.endpoints if endpoint.operation_id == operation_id)


def test_extract_endpoints(insurance_spec: ApiSpec) -> None:
    discovered = {
        (endpoint.method, endpoint.path, endpoint.operation_id) for endpoint in insurance_spec.endpoints
    }
    assert discovered == {
        (HttpMethod.GET, "/customers/{customerId}", "getCustomer"),
        (HttpMethod.GET, "/policies", "searchPolicies"),
        (HttpMethod.POST, "/policies", "createPolicy"),
        (HttpMethod.GET, "/policies/{policyId}", "getPolicy"),
        (HttpMethod.PUT, "/policies/{policyId}", "updatePolicy"),
        (HttpMethod.DELETE, "/policies/{policyId}", "cancelPolicy"),
    }


def test_extract_query_parameters(insurance_spec: ApiSpec) -> None:
    search = _endpoint(insurance_spec, "searchPolicies")
    query = {
        param.name: param
        for param in search.parameters
        if param.location == ParameterLocation.QUERY
    }

    assert set(query) == {"customerId", "status", "policyType", "page", "pageSize"}
    assert all(param.required is False for param in query.values())
    assert query["customerId"].schema_definition is not None
    assert query["customerId"].schema_definition.type == "integer"
    assert query["customerId"].schema_definition.minimum == 1
    assert query["status"].schema_definition is not None
    assert query["status"].schema_definition.ref == "#/components/schemas/PolicyStatus"
    assert query["page"].schema_definition is not None
    assert query["page"].schema_definition.default == 1
    assert query["pageSize"].schema_definition is not None
    assert query["pageSize"].schema_definition.minimum == 1
    assert query["pageSize"].schema_definition.maximum == 100
    assert query["pageSize"].schema_definition.default == 20


def test_extract_path_parameters(insurance_spec: ApiSpec) -> None:
    customer_id = _endpoint(insurance_spec, "getCustomer").parameters[0]
    assert customer_id.name == "customerId"
    assert customer_id.location == ParameterLocation.PATH
    assert customer_id.required is True
    assert customer_id.schema_definition is not None
    assert customer_id.schema_definition.type == "integer"
    assert customer_id.schema_definition.minimum == 1

    policy_id = _endpoint(insurance_spec, "getPolicy").parameters[0]
    assert policy_id.name == "policyId"
    assert policy_id.location == ParameterLocation.PATH
    assert policy_id.required is True
    assert policy_id.schema_definition is not None
    assert policy_id.schema_definition.type == "string"
    assert policy_id.schema_definition.pattern == r"^POL-[0-9]{8}$"


def test_extract_request_bodies(insurance_spec: ApiSpec) -> None:
    create = _endpoint(insurance_spec, "createPolicy")
    assert create.request_body is not None
    assert create.request_body.ref == "#/components/schemas/CreatePolicyRequest"

    update = _endpoint(insurance_spec, "updatePolicy")
    assert update.request_body is not None
    assert update.request_body.ref == "#/components/schemas/UpdatePolicyRequest"

    assert _endpoint(insurance_spec, "searchPolicies").request_body is None
    assert _endpoint(insurance_spec, "getPolicy").request_body is None
    assert _endpoint(insurance_spec, "cancelPolicy").request_body is None


def test_extract_response_codes(insurance_spec: ApiSpec) -> None:
    create = _endpoint(insurance_spec, "createPolicy")
    assert set(create.responses) == {"201", "400", "404", "409"}
    assert create.responses["201"].ref == "#/components/schemas/Policy"

    assert set(_endpoint(insurance_spec, "cancelPolicy").responses) == {"204", "404", "409"}
    assert set(_endpoint(insurance_spec, "searchPolicies").responses) == {"200", "400"}
    assert set(_endpoint(insurance_spec, "getCustomer").responses) == {"200", "404"}


def test_extract_schemas(insurance_spec: ApiSpec) -> None:
    assert set(insurance_spec.schemas) == {
        "PolicyStatus",
        "PolicyType",
        "Customer",
        "CreatePolicyRequest",
        "UpdatePolicyRequest",
        "Policy",
    }
    assert insurance_spec.schemas["Customer"].type == "object"
    assert insurance_spec.schemas["PolicyStatus"].type == "string"
    assert insurance_spec.schemas["CreatePolicyRequest"].properties is not None
    assert set(insurance_spec.schemas["CreatePolicyRequest"].properties) == {
        "customerId",
        "policyType",
        "effectiveDate",
        "expirationDate",
        "premium",
        "deductible",
        "coverageAmount",
    }


def test_extract_required_fields(insurance_spec: ApiSpec) -> None:
    assert insurance_spec.schemas["Customer"].required == [
        "customerId",
        "firstName",
        "lastName",
        "email",
    ]
    assert insurance_spec.schemas["CreatePolicyRequest"].required == [
        "customerId",
        "policyType",
        "effectiveDate",
        "expirationDate",
        "premium",
    ]
    assert insurance_spec.schemas["Policy"].required == [
        "policyId",
        "customerId",
        "policyType",
        "status",
        "effectiveDate",
        "expirationDate",
        "premium",
    ]
    assert insurance_spec.schemas["UpdatePolicyRequest"].required == []

    search_body = _endpoint(insurance_spec, "searchPolicies").responses["200"]
    assert search_body.required == ["policies", "total"]


def test_extract_enums(insurance_spec: ApiSpec) -> None:
    assert insurance_spec.schemas["PolicyStatus"].enum == ["QUOTE", "ACTIVE", "EXPIRED", "CANCELLED"]
    assert insurance_spec.schemas["PolicyType"].enum == ["AUTO", "HOME", "LIFE"]


def test_extract_formats(insurance_spec: ApiSpec) -> None:
    customer = insurance_spec.schemas["Customer"].properties
    create = insurance_spec.schemas["CreatePolicyRequest"].properties
    assert customer is not None
    assert create is not None
    assert customer["email"].format == "email"
    assert create["effectiveDate"].format == "date"
    assert create["expirationDate"].format == "date"
    assert create["premium"].format == "double"
    assert create["deductible"].format == "double"


def test_extract_constraints(insurance_spec: ApiSpec) -> None:
    customer = insurance_spec.schemas["Customer"].properties
    create = insurance_spec.schemas["CreatePolicyRequest"].properties
    assert customer is not None
    assert create is not None

    assert customer["firstName"].min_length == 1
    assert customer["firstName"].max_length == 50
    assert customer["phone"].pattern == r"^\+?[1-9][0-9]{7,14}$"
    assert customer["customerId"].minimum == 1

    assert create["premium"].minimum == 0.01
    assert create["premium"].maximum == 1_000_000
    assert create["coverageAmount"].minimum == 1000
    assert create["coverageAmount"].maximum == 10_000_000

    policy_id = insurance_spec.schemas["Policy"].properties
    assert policy_id is not None
    assert policy_id["policyId"].pattern == r"^POL-[0-9]{8}$"


def test_extract_refs(insurance_spec: ApiSpec) -> None:
    create_props = insurance_spec.schemas["CreatePolicyRequest"].properties
    policy_props = insurance_spec.schemas["Policy"].properties
    assert create_props is not None
    assert policy_props is not None
    assert create_props["policyType"].ref == "#/components/schemas/PolicyType"
    assert policy_props["policyType"].ref == "#/components/schemas/PolicyType"
    assert policy_props["status"].ref == "#/components/schemas/PolicyStatus"

    assert _endpoint(insurance_spec, "getCustomer").responses["200"].ref == "#/components/schemas/Customer"
    assert _endpoint(insurance_spec, "createPolicy").request_body is not None
    assert _endpoint(insurance_spec, "createPolicy").request_body.ref == "#/components/schemas/CreatePolicyRequest"

    status = next(
        param
        for param in _endpoint(insurance_spec, "searchPolicies").parameters
        if param.name == "status"
    )
    assert status.schema_definition is not None
    assert status.schema_definition.ref == "#/components/schemas/PolicyStatus"


def test_extract_nested_arrays(insurance_spec: ApiSpec, petstore_spec: ApiSpec) -> None:
    search_body = _endpoint(insurance_spec, "searchPolicies").responses["200"]
    assert search_body.type == "object"
    assert search_body.properties is not None
    policies = search_body.properties["policies"]
    assert policies.type == "array"
    assert policies.items is not None
    assert policies.items.ref == "#/components/schemas/Policy"

    pets = _endpoint(petstore_spec, "getPets").responses["200"]
    assert pets.type == "array"
    assert pets.items is not None
    assert pets.items.ref == "#/components/schemas/Pet"
