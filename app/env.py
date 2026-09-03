from dotenv import load_dotenv

from app.services.openapi_ingest import PROJECT_ROOT


def load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
