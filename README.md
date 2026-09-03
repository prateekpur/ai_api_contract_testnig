# AI API Contract Testing

Ingest an OpenAPI YAML spec and discover endpoints, parameters, request bodies, responses, and schemas.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

The API is at [http://127.0.0.1:8000](http://127.0.0.1:8000). Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/specs/ingest` | Parse a YAML spec and store the discovered API |
| `GET` | `/specs` | List ingested specs |
| `GET` | `/specs/{id}` | Full discovered spec |
| `GET` | `/specs/{id}/endpoints` | Discovered operations only |

Ingest from a project-relative path:

```bash
curl -X POST "http://127.0.0.1:8000/specs/ingest?path=sample_specs/petstore.yaml"
curl -X POST "http://127.0.0.1:8000/specs/ingest?path=sample_specs/insurance_api.yaml"
```

Or upload a file:

```bash
curl -X POST "http://127.0.0.1:8000/specs/ingest" -F "file=@sample_specs/petstore.yaml"
```

If `path` and `file` are omitted, ingest defaults to `sample_specs/petstore.yaml`. Spec paths must stay inside the project directory.

## Sample specs

- `sample_specs/petstore.yaml` — pets CRUD
- `sample_specs/insurance_api.yaml` — policies and customers, with query params, enums, formats, and constraints

## What the parser extracts

- Title, version, description, base URL
- HTTP operations (`operationId`, path, method, tags)
- Path and query parameters
- Request bodies and response status codes
- Component schemas, `$ref`s, required fields, enums, formats, and constraints
- Nested arrays (for example `policies[]` or `Pet[]`)

## Tests

```bash
pytest
```

Parser coverage lives in `tests/test_openapi_extract.py` (endpoints, parameters, bodies, schemas, refs, nested arrays). HTTP ingest is covered in `tests/test_openapi_ingest.py`.

## Project layout

```
app/
  main.py                 FastAPI app
  routers/specs.py        Ingest and discovery routes
  services/openapi_ingest.py   YAML → ApiSpec parser
  schemas/schemas.py      ApiSpec, Endpoint, Parameter, SchemaDefinition
sample_specs/
tests/
```
