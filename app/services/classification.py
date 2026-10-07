from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.schemas import ApiSpec, Determinism, Endpoint, TestCase, TestSource

_SECURITY = re.compile(
    r"sql|xss|injection|idor|auth.?bypass|rate.?limit|zip.?bomb|session.?manip",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TestClassification:
    """Source, determinism, and why a generated test was labeled that way."""

    test_source: TestSource
    determinism: Determinism
    reason: str


def classify_generated_test(
    case: TestCase,
    spec: ApiSpec,
    endpoint: Endpoint,
    request_valid: bool,
) -> TestClassification:
    """Label one generated test. Does not change expected status, body, or case type."""
    del spec
    if _is_security(case):
        return TestClassification(
            test_source=TestSource.SECURITY_INFERENCE,
            determinism=Determinism.CONDITIONAL,
            reason="security outcome depends on deployment and auth configuration",
        )
    if case.test_source == TestSource.SEMANTIC_INFERENCE:
        return TestClassification(
            test_source=TestSource.SEMANTIC_INFERENCE,
            determinism=Determinism.INFERRED,
            reason="semantic workflow is inferred from resource lifecycle, not a single contract rule",
        )
    success = 200 <= case.expected_status < 300
    declared = str(case.expected_status) in endpoint.responses
    if request_valid and success and declared:
        return TestClassification(
            test_source=TestSource.CONTRACT,
            determinism=Determinism.DETERMINISTIC,
            reason="request satisfies the contract and expects a declared success status",
        )
    if declared and not request_valid:
        return TestClassification(
            test_source=TestSource.CONTRACT,
            determinism=Determinism.INFERRED,
            reason="schema violation is expected to yield a declared error status",
        )
    return TestClassification(
        test_source=TestSource.CONTRACT,
        determinism=Determinism.UNKNOWN,
        reason="specification does not determine this outcome from the request schema",
    )


def _is_security(case: TestCase) -> bool:
    if case.test_source == TestSource.SECURITY_INFERENCE:
        return True
    return (
        _SECURITY.search(case.name) is not None
        or _SECURITY.search(case.description or "") is not None
    )
