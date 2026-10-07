from __future__ import annotations

from functools import lru_cache
from typing import NamedTuple

import pytest

from app.prompts.loader import load_api_spec
from app.schemas.schemas import ApiSpec, HttpMethod, TestCase, TestCaseType, TestData
from app.services.pipeline import finalize_cases
from app.services.validation import REJECT_CODES, validate_generated_suite, validate_generated_test

EVENT_SPEC = "sample_specs/arbitary_api.yaml"
PETSTORE_SPEC = "sample_specs/petstore.yaml"
INSURANCE_SPEC = "sample_specs/insurance_api.yaml"


@lru_cache
def _spec(path: str) -> ApiSpec:
    _, spec = load_api_spec(path)
    return spec


def _case(
    name: str,
    path: str,
    method: HttpMethod,
    *,
    status: int,
    case_type: TestCaseType = TestCaseType.HAPPY_PATH,
    body: object | None = None,
    path_params: dict | None = None,
    query_params: dict | None = None,
    mutated_field: str | None = None,
    constraint: str | None = None,
) -> TestCase:
    return TestCase(
        name=name,
        endpoint_path=path,
        method=method,
        test_data=TestData(
            body=body,
            path_params=path_params or {},
            query_params=query_params or {},
        ),
        expected_status=status,
        case_type=case_type,
        mutated_field=mutated_field,
        constraint=constraint,
    )


def _event_body(**overrides: object) -> dict:
    body = {
        "name": "Summit",
        "category": "CONFERENCE",
        "startDate": "2026-11-01T10:00:00Z",
        "capacity": 100,
    }
    body.update(overrides)
    return body


def _pet_body(**overrides: object) -> dict:
    body = {"name": "ab", "species": "DOG"}
    body.update(overrides)
    return body


def _policy_body(**overrides: object) -> dict:
    body = {
        "customerId": 1,
        "policyType": "AUTO",
        "effectiveDate": "2026-01-01",
        "expirationDate": "2027-01-01",
        "premium": 1200.5,
    }
    body.update(overrides)
    return body


class KnownDefect(NamedTuple):
    spec_file: str
    case: TestCase
    codes: frozenset[str]
    reject: bool


# Deliberately corrupted generated tests. Each entry is a defect the validator
# must catch; reject=True means the generation pipeline must drop the case.
KNOWN_DEFECTS = [
    KnownDefect(
        EVENT_SPEC,
        _case(
            "createEvent_invalid_datetime_expects_201",
            "/events",
            HttpMethod.POST,
            status=201,
            body=_event_body(startDate="ab"),
        ),
        frozenset({"format", "happy_path_invalid_request", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        EVENT_SPEC,
        _case(
            "createEvent_invalid_category_expects_201",
            "/events",
            HttpMethod.POST,
            status=201,
            body=_event_body(category="PARTY"),
        ),
        frozenset({"enum", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        EVENT_SPEC,
        _case(
            "createEvent_capacity_over_max_expects_201",
            "/events",
            HttpMethod.POST,
            status=201,
            body=_event_body(capacity=10001),
        ),
        frozenset({"maximum", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        EVENT_SPEC,
        _case(
            "registerAttendee_invalid_email_expects_201",
            "/events/{eventId}/attendees",
            HttpMethod.POST,
            status=201,
            path_params={"eventId": "{{eventId}}"},
            body={"name": "Ada", "email": "not-an-email"},
        ),
        frozenset({"format", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        EVENT_SPEC,
        _case(
            "getEvent_invalid_id_expects_200",
            "/events/{eventId}",
            HttpMethod.GET,
            status=200,
            path_params={"eventId": "not-an-id"},
        ),
        frozenset({"pattern", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        EVENT_SPEC,
        _case(
            "createEvent_happy_path_expects_400",
            "/events",
            HttpMethod.POST,
            status=400,
            body=_event_body(),
        ),
        frozenset({"happy_path_error_status"}),
        True,
    ),
    KnownDefect(
        PETSTORE_SPEC,
        _case(
            "createPet_invalid_species_expects_201",
            "/pets",
            HttpMethod.POST,
            status=201,
            body=_pet_body(species="FISH"),
        ),
        frozenset({"enum", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        PETSTORE_SPEC,
        _case(
            "createPet_name_too_short_expects_201",
            "/pets",
            HttpMethod.POST,
            status=201,
            body=_pet_body(name="a"),
        ),
        frozenset({"minLength", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        PETSTORE_SPEC,
        _case(
            "createPet_age_over_max_expects_201",
            "/pets",
            HttpMethod.POST,
            status=201,
            body=_pet_body(age=31),
        ),
        frozenset({"maximum", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        PETSTORE_SPEC,
        _case(
            "createPet_missing_name_expects_201",
            "/pets",
            HttpMethod.POST,
            status=201,
            body={"species": "DOG"},
        ),
        frozenset({"required", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        PETSTORE_SPEC,
        _case(
            "getPet_id_below_minimum_expects_200",
            "/pets/{petId}",
            HttpMethod.GET,
            status=200,
            path_params={"petId": 0},
        ),
        frozenset({"minimum", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        PETSTORE_SPEC,
        _case(
            "createPet_negative_with_valid_201",
            "/pets",
            HttpMethod.POST,
            status=201,
            case_type=TestCaseType.NEGATIVE,
            body=_pet_body(),
        ),
        frozenset({"negative_success_status"}),
        True,
    ),
    KnownDefect(
        INSURANCE_SPEC,
        _case(
            "createPolicy_invalid_date_expects_201",
            "/policies",
            HttpMethod.POST,
            status=201,
            body=_policy_body(effectiveDate="ab"),
        ),
        frozenset({"format", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        INSURANCE_SPEC,
        _case(
            "createPolicy_datetime_in_date_field_expects_201",
            "/policies",
            HttpMethod.POST,
            status=201,
            body=_policy_body(effectiveDate="2026-01-01T00:00:00Z"),
        ),
        frozenset({"format", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        INSURANCE_SPEC,
        _case(
            "createPolicy_invalid_type_expects_201",
            "/policies",
            HttpMethod.POST,
            status=201,
            body=_policy_body(policyType="BOAT"),
        ),
        frozenset({"enum", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        INSURANCE_SPEC,
        _case(
            "createPolicy_premium_below_min_expects_201",
            "/policies",
            HttpMethod.POST,
            status=201,
            body=_policy_body(premium=0),
        ),
        frozenset({"minimum", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        INSURANCE_SPEC,
        _case(
            "getPolicy_invalid_id_expects_200",
            "/policies/{policyId}",
            HttpMethod.GET,
            status=200,
            path_params={"policyId": "POL-1"},
        ),
        frozenset({"pattern", "success_status_invalid_request"}),
        True,
    ),
    KnownDefect(
        INSURANCE_SPEC,
        _case(
            "searchPolicies_pageSize_over_max_expects_200",
            "/policies",
            HttpMethod.GET,
            status=200,
            query_params={"pageSize": 101},
        ),
        frozenset({"maximum", "success_status_invalid_request"}),
        True,
    ),
]

VALID_CASES = [
    (EVENT_SPEC, _case("createEvent_happy_path", "/events", HttpMethod.POST, status=201, body=_event_body())),
    (PETSTORE_SPEC, _case("createPet_happy_path", "/pets", HttpMethod.POST, status=201, body=_pet_body())),
    (
        INSURANCE_SPEC,
        _case("createPolicy_happy_path", "/policies", HttpMethod.POST, status=201, body=_policy_body()),
    ),
]


@pytest.mark.parametrize("defect", KNOWN_DEFECTS, ids=lambda item: item.case.name)
def test_validator_catches_known_defect(defect: KnownDefect) -> None:
    report = validate_generated_test(defect.case, _spec(defect.spec_file))
    codes = {item.code for item in report.violations}
    missing = defect.codes - codes
    assert not missing, f"{defect.case.name}: missing {sorted(missing)} in {sorted(codes)}"
    assert report.ok is False
    if defect.reject:
        assert codes & REJECT_CODES


@pytest.mark.parametrize("defect", KNOWN_DEFECTS, ids=lambda item: item.case.name)
def test_pipeline_rejects_corrupted_tests(defect: KnownDefect) -> None:
    result = finalize_cases([defect.case], [], _spec(defect.spec_file))
    if defect.reject:
        assert result.cases == []
        assert any(issue.test_name == defect.case.name for issue in result.validation_errors)
        assert any(defect.case.name in item for item in result.dropped)
        return
    assert [case.name for case in result.cases] == [defect.case.name]


@pytest.mark.parametrize(
    "spec_file, case",
    VALID_CASES,
    ids=[case.name for _, case in VALID_CASES],
)
def test_validator_accepts_valid_happy_paths_across_apis(
    spec_file: str,
    case: TestCase,
) -> None:
    report = validate_generated_test(case, _spec(spec_file))
    assert report.ok, report.messages()
    assert report.request_valid is True
    result = finalize_cases([case], [], _spec(spec_file))
    assert [item.name for item in result.cases] == [case.name]
    assert result.validation_errors == []


def test_suites_flag_corrupted_cases_and_keep_valid_ones() -> None:
    valid_event = VALID_CASES[0][1]
    valid_pet = VALID_CASES[1][1]
    valid_policy = VALID_CASES[2][1]
    corrupt_event = KNOWN_DEFECTS[0].case
    corrupt_pet = next(item.case for item in KNOWN_DEFECTS if item.case.name.startswith("createPet_invalid_species"))
    corrupt_policy = next(
        item.case for item in KNOWN_DEFECTS if item.case.name.startswith("createPolicy_invalid_date")
    )

    event_suite = validate_generated_suite([valid_event, corrupt_event], _spec(EVENT_SPEC))
    pet_suite = validate_generated_suite([valid_pet, corrupt_pet], _spec(PETSTORE_SPEC))
    policy_suite = validate_generated_suite([valid_policy, corrupt_policy], _spec(INSURANCE_SPEC))

    for suite in (event_suite, pet_suite, policy_suite):
        assert suite.reports[0].ok
        assert suite.reports[1].ok is False
        assert suite.ok is False

    assert any(item.code == "format" for item in event_suite.violations)
    assert any(item.code == "enum" for item in pet_suite.violations)
    assert any(item.code == "format" for item in policy_suite.violations)


def test_invalid_datetime_expecting_201_is_recorded_as_a_format_defect() -> None:
    defect = KNOWN_DEFECTS[0]
    assert defect.case.expected_status == 201
    assert defect.case.test_data.body["startDate"] == "ab"
    report = validate_generated_test(defect.case, _spec(EVENT_SPEC))
    format_issue = next(item for item in report.violations if item.code == "format")
    assert format_issue.path == "$.startDate"
    assert "date-time" in format_issue.message
    assert format_issue.test_name == "createEvent_invalid_datetime_expects_201"


def test_legitimate_negative_schema_test_is_not_rejected() -> None:
    case = _case(
        "createPet_invalid_species_expects_400",
        "/pets",
        HttpMethod.POST,
        status=400,
        case_type=TestCaseType.NEGATIVE,
        body=_pet_body(species="FISH"),
    )
    report = validate_generated_test(case, _spec(PETSTORE_SPEC))
    assert any(item.code == "enum" for item in report.violations)
    assert "success_status_invalid_request" not in {item.code for item in report.violations}
    result = finalize_cases([case], [], _spec(PETSTORE_SPEC))
    assert [item.name for item in result.cases] == [case.name]
    assert result.validation_errors == []
    assert result.cases[0].expected_status == 400
    assert result.cases[0].test_data.body["species"] == "FISH"
