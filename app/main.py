from fastapi import FastAPI

from app.env import load_env
from app.routers.specs import router as specs_router
from app.routers.tests import router as tests_router

load_env()

app = FastAPI(title="AI API Contract Testing")
app.include_router(specs_router)
app.include_router(tests_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
