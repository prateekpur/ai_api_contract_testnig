from __future__ import annotations

from pathlib import Path

from app.schemas.schemas import TestCase
from app.services.openapi_ingest import resolve_spec_path


def export_test_cases(cases: list[TestCase], output_path: str | Path) -> Path:
    if not cases:
        raise ValueError("No scenarios to export. Generate scenarios first.")
    dest = resolve_spec_path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = [case.model_dump(mode="json") for case in cases]
    dest.write_text(_to_json(payload) + "\n")
    return dest


def _to_json(payload: list[dict]) -> str:
    import json

    return json.dumps(payload, indent=2)
