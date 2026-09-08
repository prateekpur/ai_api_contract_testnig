from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.schemas import TestCase
from app.services.happy_path_tests import generate_all_tests

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
