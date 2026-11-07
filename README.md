# CollectFlow API

FastAPI backend for CollectFlow, a lending collections portal. This bootstrap provides a read-only account portfolio, aging buckets, search, pagination, and a local SQLite database. All demo names are fictional. Amounts are integer Indonesian rupiah (IDR).

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Meefx/Hackathon-Backend.git
cd Hackathon-Backend
cp .env.example .env
uv sync
uv run python -m app.seed
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Swagger UI: http://localhost:8000/docs. OpenAPI: http://localhost:8000/openapi.json. The database is created on startup. Seeding is explicit and idempotent; existing rows are preserved. Demo due dates are relative to the first seed date, and aging uses the Asia/Jakarta calendar.

Frontend repository: [Hackathon-Frontend](https://github.com/Meefx/Hackathon-Frontend). Run it at http://localhost:3000. Change `CORS_ORIGINS` in `.env` if using a different frontend origin (JSON array).

## API contract

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | Service liveness |
| GET | `/api/v1/dashboard` | Portfolio totals and four aging buckets |
| GET | `/api/v1/accounts` | Accounts with optional `search`, `status`, `limit` (1–100), `offset` (0+) |
| GET | `/api/v1/accounts/{id}` | Single account, or HTTP 404 |

Account lists return `{items, total, limit, offset}`, sorted by oldest due date then ID. Search matches borrower name or loan reference without case sensitivity. Status values: `current` (0 days past due, including future dates), `early` (1–30), `late` (31–90), `critical` (91+). Invalid query parameters return HTTP 422. Dashboard totals always cover the entire portfolio.

Pydantic models in `app/schemas.py` and the generated `/openapi.json` are the API contract. TypeScript types in the frontend's `src/lib/api.ts` must be updated when this contract changes.

## Checks

```sh
uv run pytest
uv run ruff check .
```

GitHub Actions runs these checks on pushes and pull requests. After the first successful `uv sync`, commit the generated `uv.lock` and use `uv sync --locked` for reproducible installs.

Storage and aging tests also run without dependencies: `python3 -m unittest discover -s tests -p test_store.py`.

## Structure

```text
app/config.py   Environment settings
app/main.py     App factory, CORS, and versioned routes
app/schemas.py  Response contract
app/store.py    SQLite storage and aging logic
app/seed.py     Explicit fictional demo seed
tests/          Storage and HTTP behavior tests
```

This is a local development foundation. Authentication, authorization, write workflows, payment integrations, and production database migrations are future work; use fictional data until access controls are implemented.
