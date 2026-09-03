from pathlib import Path

from app.services.openapi_ingest import resolve_spec_path

PROMPT_PATH = Path(__file__).with_name("happy_path_tests.pompt")


def load_happy_path_prompt(spec_file: str | Path) -> str:
    """Build the happy-path prompt using a user-supplied ingest JSON file."""
    spec_path = resolve_spec_path(spec_file)
    if not spec_path.is_file():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    template = PROMPT_PATH.read_text()
    return template.replace("{{SPEC_FILE}}", str(spec_path)).replace(
        "{{SPEC_JSON}}",
        spec_path.read_text(),
    )
