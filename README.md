# AI API Contract Testing

Ingest an OpenAPI YAML spec, generate tests from it, export them as JSON, and run them against a live API.

Generation is dual-track. Both tracks emit the same `TestCase` shape, so export and run stay unchanged:

1. **Contract** — schema-driven cases: happy path, required/enum/boundary/null/type, query/path, documented error statuses
2. **Semantic** — business and workflow cases: create→get, create→delete, 404 after delete, cross-endpoint ids via `{{placeholders}}` and `dependencies`

The lists are merged (contract first), fingerprint-deduped, then checked programmatically. Invalid or duplicate cases are dropped and listed in the generate log.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set the generation API key in `.env`. The optional model defaults to `composer-2.5`.

## CLI

```bash
python -m app
```

1. **Generate all tests** — path to OpenAPI YAML or ingest JSON (for example `sample_specs/petstore.yaml`)
2. **Export scenarios** — writes JSON (default `exports/happy_path_tests.json`)
3. **Run scenarios** — loads that JSON and hits the API base URL (for example `http://localhost:8000`)
4. **Exit**

Option 1 runs both prompts, then merge → dedupe → validate. It prints:

```
Contract: N, semantic: M, kept: K, dropped: D
Dropped <name>: <reason>
```

Generate before export in the same session. Run can load an existing export file without generating first.

## How generation works

```
OpenAPI ingest
  → contract_tests.pompt
  → semantic_tests.pompt
  → parse / expand $ref
  → merge
  → fingerprint dedupe
  → self-validation
  → final TestCase list
```

**Contract track** (`app/prompts/contract_tests.pompt`) does not invent statuses. Success uses the lowest declared 2xx; errors use a documented 4xx.

**Semantic track** (`app/prompts/semantic_tests.pompt`) does not re-list field-level contract cases. It uses existing `case_type` values (`happy_path` / `negative`).

**Dedupe** keeps the first case with the same `(method, path, expected_status, case_type, canonical test_data)`. Contract wins when both tracks invent the same request.

**Self-validation** (no extra model call) drops a case when:

- the name is not unique
- `endpoint_path` + `method` are not on the spec
- `expected_status` is not a declared response
- a required path param is missing
- a `{{var}}` has no matching `dependencies` entry
- `source_test` is not in the final list

## HTTP API

```bash
uvicorn app.main:app --reload
```

The API is at [http://127.0.0.1:8000](http://127.0.0.1:8000). Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/specs/ingest` | Parse a YAML spec and store the discovered API |
| `GET` | `/specs` | List ingested specs |
| `GET` | `/specs/{id}` | Full discovered spec |
| `GET` | `/specs/{id}/endpoints` | Discovered operations only |
| `POST` | `/tests/happy-path` | Generate tests (same dual-track pipeline as the CLI) |

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

Generate tests:

```bash
curl -X POST "http://127.0.0.1:8000/tests/happy-path?path=sample_specs/petstore.yaml"
```

## Sample specs

- `sample_specs/petstore.yaml` — pets CRUD (`http://localhost:8000`)
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

Coverage includes OpenAPI parse and ingest, contract and semantic prompt loading, TestCase parse and `$ref` expansion, merge/dedupe/validation, CLI generate/export/run, placeholder interpolation, extraction, schema validation, and the workflow runner.

## Project layout

```
app/
  cli.py                         Generate, export, and run menu
  main.py                        FastAPI app
  prompts/contract_tests.pompt   Field-level contract prompt
  prompts/semantic_tests.pompt   Workflow / business-scenario prompt
  prompts/loader.py              Inject spec JSON into prompts
  routers/specs.py               Ingest and discovery routes
  routers/tests.py               Test generation route
  schemas/schemas.py             ApiSpec, Endpoint, TestCase, TestResult
  services/openapi_ingest.py     YAML → ApiSpec parser
  services/happy_path_tests.py   Run both tracks and parse TestCase JSON
  services/pipeline.py           Merge, fingerprint dedupe, self-validation
  services/export.py             Write scenarios to JSON
  services/runner.py             Execute chained tests against a live API
sample_specs/
tests/
```
