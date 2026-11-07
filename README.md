# CollectFlow API

FastAPI backend for CollectFlow, a lending collections portal. This bootstrap provides a read-only account portfolio, aging buckets, search, pagination, and a local SQLite database. All demo names are fictional. Amounts are integer Indonesian rupiah (IDR).

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Meefx/Hackathon-Backend.git
cd Hackathon-Backend
cp .env.example .env
uv sync --locked
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

GitHub Actions runs these checks on pushes and pull requests. Dependencies are pinned in `uv.lock`; use `uv sync --locked` for reproducible installs.

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

## Erwan's evidence-control MVP

The standalone trust service adds a PostgreSQL KMS, source approvals, scoped retrieval,
weighted source-to-template dependencies, revocation, deterministic guardrails and a
synthetic dispatch gate. It does not replace the team's SQLite portfolio API.
All trust workflows use synthetic data. Live channels and payment handling do not exist.

Start PostgreSQL17 with pgvector, for example:

```sh
docker compose -f docker-compose.trust.yml up -d
uv sync --locked
export TRUST_DATABASE_URL=postgresql://graceline@127.0.0.1:55432/graceline_trust
uv run python -m app.trust.demo --serve --port 8001
```

Open [the evidence-control console](http://127.0.0.1:8001/console),
[Swagger](http://127.0.0.1:8001/docs), or [readiness](http://127.0.0.1:8001/ready).
The demo launcher generates ephemeral in-memory signing keys and fixes the simulated
clock to Monday 9 November 2026, 10:00 WIB. Demo identity selection is enabled only
by this explicit launcher. Restarting invalidates its sessions and approvals in memory.
The database retains artifact approvals; dispatch approval tokens are never persisted.
If port 8001 is busy, select another `--port`.

For native PostgreSQL, use any isolated PostgreSQL17 database with the vector
extension installed and set TRUST_DATABASE_URL to its DSN. Docker is optional.
Schema creation is explicit during the trust app's lifespan. A missing database,
missing vector extension or invalid signing configuration fails startup.

Run the scripted API scenario and verification:

```sh
uv run python -m app.trust.demo --report docs/evidence/demo.json
export TRUST_TEST_DATABASE_URL=postgresql://graceline@127.0.0.1:55432/graceline_trust
uv run pytest -q
uv run ruff check .
uv run mypy app
uv run python -m app.trust.benchmark --requests 100
```

Use a separate disposable test database when sharing a development database with a
team. Integration tests deliberately fail if TRUST_TEST_DATABASE_URL is missing;
they never substitute SQLite or silently skip safety checks.

For embedding in the team's backend, `app.trust.app.create_trust_app` accepts explicit
settings, clock and classifier dependencies. Keep its lifespan active when mounting.
The module uses environment variables directly and never reads local `.env` files.
The demo-auth interface must remain disabled outside an explicit local simulation.
Every mutating POST except demo login requires an `Idempotency-Key` UUID. Search is
always reevaluated against current source status. Principals/products come from the
signed session, not request bodies. No direct dispatch endpoint exists.

Review [scope and acceptance](docs/TRUST_MVP.md), [security boundaries](docs/SECURITY.md)
and [decisions](docs/DECISIONS.md) before integration. The local hash-vector baseline
is **not semantic embeddings**, Layer2 is mocked, and scripted demo approvals are
**not legal review**. The full five-agent product and original 50-persona collection
evaluation remain outside this data-module contribution.

Critical retrieval also expands source context and verifies hashes within an explicit
budget. Oversized critical evidence stops for review. The audit chain intentionally
serializes write transactions; the included sequential local benchmark is not a
production load or live-model latency claim.
