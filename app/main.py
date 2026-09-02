from fastapi import FastAPI

app = FastAPI(title="AI API Contract Testing")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
