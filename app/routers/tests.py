from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.schemas import ExecutionResult, ExecutorConfig, RunTestsRequest, TestCase
from app.services.happy_path_tests import generate_all_tests
from app.services.runner import ApiExecutor, load_test_cases

router = APIRouter(prefix="/tests", tags=["tests"])


@router.post("/happy-path", response_model=list[TestCase])
def generate_happy_path_tests(
    path: str = Query(..., description="Project-relative path to an ingest JSON file"),
) -> list[TestCase]:
    try:
        return generate_all_tests(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/run", response_model=ExecutionResult)
def run_tests(payload: RunTestsRequest) -> ExecutionResult:
    try:
        cases = _cases_from_payload(payload)
        config = ExecutorConfig(
            base_url=payload.base_url,
            timeout_s=payload.timeout_s,
            default_headers=payload.default_headers,
        )
        return ApiExecutor(config).execute(cases)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _cases_from_payload(payload: RunTestsRequest) -> list[TestCase]:
    if payload.cases:
        return payload.cases
    if payload.path:
        return load_test_cases(payload.path)
    raise ValueError("cases or path is required")
