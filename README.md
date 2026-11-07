# CollectFlow API

FastAPI backend for CollectFlow, a lending collections portal. The read-only portfolio provides account search, pagination, dashboard totals, and aging buckets backed by PostgreSQL. All demo names are fictional. Amounts are integer Indonesian rupiah (IDR). The broader Graceline agent workflow remains planned.

## Run with Docker Compose

Requires Docker Engine with Compose v2. Run commands from this repository.

```sh
cp .env.example .env
docker compose up --build -d --wait
docker compose exec backend uv run --no-sync python -m app.seed
```

Compose starts PostgreSQL 17, waits for database health, runs the versioned Alembic migration in the one-shot `migrate` service, and starts the API. Seeding is a separate, explicit operation. Rerunning it preserves existing rows and their first-seeded due dates, including concurrent runs.

The API listens at [http://localhost:8000](http://localhost:8000). Open [Swagger UI](http://localhost:8000/docs) or [OpenAPI](http://localhost:8000/openapi.json). PostgreSQL and backend published ports bind to loopback. `POSTGRES_PORT` and `BACKEND_PORT` in `.env` can change the host ports. The named `postgres_data` volume persists portfolio data across container recreation.

```sh
docker compose ps -a
docker compose logs migrate backend
docker compose down
```

`down` retains the database volume. Removing that volume deletes the database. The example credentials are for local synthetic demos. Set matching `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` before the first initialization when using other credentials. PostgreSQL initialization variables apply only to an empty volume. Compose constructs its internal connection URL from these variables. Passwords used directly in this Compose URL must be URL-safe. For host connections, percent-encode reserved characters in `DATABASE_URL`.

## Run Python on the host

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Compose can provide just PostgreSQL.

```sh
cp .env.example .env
uv sync --locked
docker compose up -d --wait postgres
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`DATABASE_URL` configures host Python connections and Alembic. Its default matches the local Compose demo. Update its host port if `POSTGRES_PORT` changes. Frontend repository [Hackathon-Frontend](https://github.com/Meefx/Hackathon-Frontend) runs at [http://localhost:3000](http://localhost:3000). Configure `CORS_ORIGINS` as a JSON array when the origin changes.

## Schema changes and legacy data

Alembic is the sole schema owner. Application startup and seed perform no DDL. Runtime queries use psycopg. SQLAlchemy is used by Alembic migration infrastructure, without an application ORM. The baseline stores balances as nonnegative `BIGINT`, due dates as PostgreSQL `DATE`, unique loan references, and an index on due date and ID.

```sh
uv run alembic current
uv run alembic heads
uv run alembic upgrade head
```

For a Compose deployment, apply a new checked-in revision explicitly before restarting the API.

```sh
docker compose build
docker compose run --rm migrate
docker compose up -d --wait backend
```

Review target settings and take a consistent backup before changing a populated database. Future changes belong in new revisions under `migrations/versions/`. Update `REQUIRED_REVISION` in `app/db/connection.py` with the deployed head so `/ready` detects an outdated schema. The baseline downgrade drops all account data. Use downgrades only on an authorized disposable target or after verifying backup and restore.

Existing `data/collectflow.db` files are left in place. To preserve a legacy SQLite portfolio, first stop legacy writers or create a consistent snapshot with SQLite's backup API, including committed WAL data. Keep that source snapshot as a backup. Migrate an empty PostgreSQL destination, then import explicitly before demo seeding.

```sh
uv run alembic upgrade head
uv run python -m app.db.import_sqlite /absolute/path/to/legacy-snapshot.db
```

The importer opens SQLite read-only, validates every row, preserves IDs, names, references, integer balances, calendar due dates, and assignments, and writes within one PostgreSQL transaction. Identical existing rows make reruns idempotent. Conflicting IDs or references abort and roll back the entire import. Invalid legacy dates or amounts abort the import. Resolve conflicts on a reviewed copy, keeping the original snapshot. Check destination row counts and important fields before retiring the source. `DATABASE_PATH` has been replaced by `DATABASE_URL`.

A Compose-only import can mount a snapshot read-only into a one-off backend container.

```sh
docker compose run --rm --volume /absolute/path/to/legacy-snapshot.db:/legacy.db:ro backend \
  uv run --no-sync python -m app.db.import_sqlite /legacy.db
```

## API contract

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | Service liveness, independent of database availability |
| GET | `/ready` | PostgreSQL and required schema readiness, HTTP 503 when unavailable |
| GET | `/api/v1/dashboard` | Portfolio totals and four aging buckets |
| GET | `/api/v1/accounts` | Accounts with optional `search`, `status`, `limit` (1–100), `offset` (0+) |
| GET | `/api/v1/accounts/{id}` | Single account, or HTTP 404 |

Account lists return `{items, total, limit, offset}`, sorted by oldest due date then ID. Search matches borrower name or loan reference without case sensitivity. Search and status filtering happen before pagination. Status values are `current` (0 days past due, including future dates), `early` (1–30), `late` (31–90), and `critical` (91+). Aging uses the Asia/Jakarta calendar. Invalid query parameters return HTTP 422. Dashboard totals always cover the entire portfolio.

Pydantic models in `app/schemas.py` and generated OpenAPI define the HTTP contract. The portfolio response fields remain compatible with the frontend's `src/lib/api.ts`.

## Checks

Tests require a dedicated PostgreSQL database whose name ends in `_test`. Each database test creates, migrates, and drops its own random schema within that database. `DATABASE_URL` from the developer environment is replaced for the test and restored afterward. The optional test service uses tmpfs storage.

```sh
uv sync --locked
docker compose --profile test up -d --wait postgres-test
TEST_DATABASE_URL=postgresql://collectflow_test:local-test-password@127.0.0.1:5433/collectflow_test \
  uv run pytest
uv run ruff check .
docker compose --profile test stop postgres-test
```

Adjust the test URL port when `TEST_POSTGRES_PORT` changes. GitHub Actions supplies its own PostgreSQL service. The suite covers migrations, database constraints and rollback, explicit concurrent/idempotent seed, legacy import preservation and conflict rollback, aging boundaries, and the existing HTTP contract. Dependencies are pinned in `uv.lock`.

## Structure

```text
app/config.py                      Environment settings
app/db/connection.py               psycopg transactions and readiness
app/db/import_sqlite.py            Explicit read-only legacy import command
app/main.py                        App factory, CORS, and HTTP routes
app/schemas.py                     Existing HTTP response contract
app/modules/accounts/repository.py Account SQL
app/modules/accounts/service.py    Aging and portfolio reads
app/modules/dashboard/service.py   Portfolio aggregation
app/seed.py                        Explicit fictional demo seed
migrations/versions/               Versioned schema history
compose.yaml                      Local PostgreSQL, migration, API, test service
tests/                            Isolated PostgreSQL and HTTP tests
```

Authentication, authorization, write workflows, agent/MCP workflows, and payment integrations remain future work. Use synthetic data for this local demo.
