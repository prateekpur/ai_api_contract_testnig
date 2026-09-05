from __future__ import annotations

import json
from pathlib import Path

from app.schemas.schemas import ApiSpec
from app.services.openapi_ingest import ingest_openapi_file, resolve_spec_path

PROMPT_PATH = Path(__file__).with_name("happy_path_tests.pompt")
_YAML_SUFFIXES = {".yaml", ".yml"}


def load_api_spec(spec_file: str | Path) -> tuple[Path, ApiSpec]:
    spec_path = resolve_spec_path(spec_file)
    if not spec_path.is_file():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")
    if spec_path.suffix.lower() in _YAML_SUFFIXES:
        return spec_path, ingest_openapi_file(spec_path)
    raw = json.loads(spec_path.read_text())
    return spec_path, ApiSpec.model_validate(raw)


def load_happy_path_prompt(spec_file: str | Path) -> str:
    """Build the happy-path prompt from a user-supplied YAML or ingest JSON file."""
    spec_path, spec = load_api_spec(spec_file)
    template = PROMPT_PATH.read_text()
    return template.replace("{{SPEC_FILE}}", str(spec_path)).replace(
        "{{SPEC_JSON}}",
        spec.model_dump_json(indent=2, by_alias=True),
    )
