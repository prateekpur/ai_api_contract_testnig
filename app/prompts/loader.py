from __future__ import annotations

import json
from pathlib import Path

from app.schemas.schemas import ApiSpec
from app.services.openapi_ingest import ingest_openapi_file, resolve_spec_path

PROMPTS_DIR = Path(__file__).parent
CONTRACT_PROMPT = PROMPTS_DIR / "contract_tests.pompt"
SEMANTIC_PROMPT = PROMPTS_DIR / "semantic_tests.pompt"
_YAML_SUFFIXES = {".yaml", ".yml"}


def load_api_spec(spec_file: str | Path) -> tuple[Path, ApiSpec]:
    spec_path = resolve_spec_path(spec_file)
    if not spec_path.is_file():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")
    if spec_path.suffix.lower() in _YAML_SUFFIXES:
        return spec_path, ingest_openapi_file(spec_path)
    raw = json.loads(spec_path.read_text())
    return spec_path, ApiSpec.model_validate(raw)


def load_prompt(spec_file: str | Path, prompt_path: Path) -> str:
    spec_path, spec = load_api_spec(spec_file)
    template = prompt_path.read_text()
    return template.replace("{{SPEC_FILE}}", str(spec_path)).replace(
        "{{SPEC_JSON}}",
        spec.model_dump_json(indent=2, by_alias=True),
    )


def load_contract_prompt(spec_file: str | Path) -> str:
    """Build the contract-test prompt from a user-supplied YAML or ingest JSON file."""
    return load_prompt(spec_file, CONTRACT_PROMPT)


def load_semantic_prompt(spec_file: str | Path) -> str:
    """Build the semantic-test prompt from a user-supplied YAML or ingest JSON file."""
    return load_prompt(spec_file, SEMANTIC_PROMPT)
