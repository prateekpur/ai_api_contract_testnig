from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.schemas.schemas import ApiSpec, Endpoint
from app.services.openapi_ingest import DEFAULT_SPEC_PATH, ingest_openapi_bytes, ingest_openapi_file

router = APIRouter(prefix="/specs", tags=["specs"])
_specs: dict[UUID, ApiSpec] = {}


@router.post("/ingest", response_model=ApiSpec)
async def ingest_spec(
    path: str | None = Query(
        default=None,
        description="Project-relative path to an OpenAPI YAML file",
    ),
    file: UploadFile | None = File(default=None),
) -> ApiSpec:
    try:
        if file is not None:
            spec = ingest_openapi_bytes(await file.read())
        else:
            spec = ingest_openapi_file(path or DEFAULT_SPEC_PATH)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _specs[spec.id] = spec
    return spec


@router.get("", response_model=list[ApiSpec])
def list_specs() -> list[ApiSpec]:
    return list(_specs.values())


@router.get("/{spec_id}", response_model=ApiSpec)
def get_spec(spec_id: UUID) -> ApiSpec:
    spec = _specs.get(spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Spec not found")
    return spec


@router.get("/{spec_id}/endpoints", response_model=list[Endpoint])
def discover_endpoints(spec_id: UUID) -> list[Endpoint]:
    spec = _specs.get(spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Spec not found")
    return spec.endpoints
