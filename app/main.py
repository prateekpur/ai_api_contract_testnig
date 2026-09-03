from fastapi import FastAPI

from app.routers.specs import router as specs_router

app = FastAPI(title="AI API Contract Testing")
app.include_router(specs_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
