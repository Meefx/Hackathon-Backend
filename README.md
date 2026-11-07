# CollectFlow API

FastAPI backend for CollectFlow, a lending collections portal. The read-only portfolio provides account search, pagination, dashboard totals, and aging buckets backed by PostgreSQL. All demo names are fictional. Amounts are integer Indonesian rupiah (IDR). Collection workflows now have persistent synthetic demo support; live provider acceptance is reported separately.

## Run with Docker Compose

Requires Docker Engine with Compose v2. Run commands from this repository.

```sh
cp .env.example .env
docker compose up --build -d --wait
docker compose exec backend uv run --no-sync python -m app.seed
```

Compose starts PostgreSQL 17 with pgvector, waits for database health, runs the versioned Alembic migration in the one-shot `migrate` service, and starts the API. Seeding is a separate, explicit operation. Rerunning it preserves existing rows and their first-seeded due dates, including concurrent runs.

The API listens at [http://localhost:8000](http://localhost:8000). Open [Swagger UI](http://localhost:8000/docs) or [OpenAPI](http://localhost:8000/openapi.json). PostgreSQL and backend published ports bind to loopback. `POSTGRES_PORT` and `BACKEND_PORT` in `.env` can change the host ports. The named `postgres_data` volume persists portfolio data across container recreation.

For a local WhatsApp connection sandbox, run `./scripts/waha-local.sh` and follow
[the WAHA local runbook](docs/integrations/waha-local.md) to pair a team test phone
and connect a synthetic case to that phone's own chat. WAHA runs on loopback
port 3002 with separate credentials and persistent sessions. Linked operator
follow-up drafts use the approved dispatch endpoint and signed inbound webhooks.
Scheduled runs use the simulated channel. The runbook covers migration, worker
startup, frontend controls, and uncertain-delivery handling.

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

Review target settings and take a consistent backup before changing a populated database. Future changes belong in new revisions under `migrations/versions/`. Update `REQUIRED_REVISION` in `app/db/connection.py` with the deployed head so `/ready` detects an outdated schema. Revision `0002_trust` refuses downgrade when any trust table contains data. An existing unversioned `trust` schema is rejected without adopting or dropping it. Preserve the old database and use a reviewed export/forward migration; never stamp it as migrated. The baseline downgrade drops all account data. Use downgrades only on an authorized disposable target or after verifying backup and restore.

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

Tests require a dedicated PostgreSQL database whose name ends in `_test`. Each database test creates, migrates, and drops its own randomly named database. The configured test role must have CREATEDB; the base test database is never cleared. This isolates the fixed `trust` schema as well as portfolio data. `DATABASE_URL` from the developer environment is replaced for the test and restored afterward. The optional test service uses tmpfs storage.

```sh
uv sync --locked
docker compose --profile test up -d --wait postgres-test
TEST_DATABASE_URL=postgresql://collectflow_test:local-test-password@127.0.0.1:5433/collectflow_test \
  uv run pytest
uv run ruff check .
uv run mypy app
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
app/modules/trust/                 Typed routes, service and repository boundary
app/modules/knowledge_sources/     Curated public PDF archive, draft staging and reviewer API
app/integrations/knowledge_sources/ Bounded HTTPS downloader and deterministic PDF extraction
app/policies/corpus/               Pinned source manifests, dates, scopes and provision locators
app/trust/                         Existing evidence and safety engine
app/services/dispatch_gate.py      Sole simulated-channel entry point
migrations/versions/               Accounts and trust versioned schema history
compose.yaml                      Local PostgreSQL, migration, API, test service
tests/                            Isolated PostgreSQL and HTTP tests
```

Remote operator authentication and payment integrations remain future work. Use synthetic data for this local demo.

## Persistent collection demo

The v2 collection API adds manager sessions, durable account runs, immutable knowledge snapshots,
message drafts, HMAC-bound compliance approvals, simulated dispatch, manual review, and chained
account audit. The existing portfolio contract remains unchanged. Run Alembic before restarting
API/worker processes. `python -m app.seed` explicitly provisions the eight fictional accounts,
synthetic verified recipients, and a `demo-policy-v1` fixture covering dates through 2027-12-31.
Unseeded/imported accounts remain ineligible until a verified recipient and eligibility are set.

```sh
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# In a separate terminal using the same backend environment:
uv run python -m app.workers.collection
```

Compose now starts a worker from the same backend image. The API accepts and persists tasks while
workers are offline. Each worker claims one task at a time using PostgreSQL leases, fencing tokens,
and session ordering. The default lease is 60 seconds. A worker whose lease expires cannot commit;
the run becomes `interrupted` and blocks later tasks in that manager session. Recovery only requeues
an interrupted task with no external admission snapshot or persisted domain side effects. Ambiguous
Hermes execution requires reconciliation; it is never automatically resubmitted. Cancel requests
remain requests until the worker/runtime confirms termination. No recovery or resume sends a message.

The demo clock is the actual Asia/Jakarta calendar. Outbound generation and dispatch require an
eligible case, verified recipient, active policy coverage, local time 08:00–20:00, no review/operator
hold, and fewer than one simulated message per account/calendar day. Inbound synthetic hardship,
dispute, and human-request scenarios can open reviews at any time. Resolving `keep_paused` retains an
operator hold; `request_resume` returns the review result and the independently evaluated resume result.

The task types are `follow_up`, `collector_brief`, and `improvement_review`. Specify `runtime_mode`
explicitly as `simulated` or `hermes`; there is no automatic fallback. The simulator uses labeled
`demo_fixture` knowledge and QA. Hermes candidates are quarantined when the live policy classifier is
unavailable; an agent QA report never grants dispatch approval. Improvement proposals remain advisory.
WhatsApp, email, and phone adapters return `channel_unavailable` and create no delivery receipts.
The only dispatch implementation is `simulated_chat`.

Gmail API connectivity is available as a separate team sandbox with Desktop OAuth,
private refresh-token storage, and a fixed synthetic test email with a durable
request-ID journal. The Integrations registry reports its configuration and
unprobed health separately. Follow the [Gmail setup guide](docs/integrations/gmail.md)
to authorize a team account and run an explicit connection test.

New endpoints and request/response schemas are visible in `/docs` and `/openapi.json`. Registry reads
use `/api/v1/agents` and `/api/v1/integrations`; histories use bounded `{items,total,limit,offset}`
pages, including `/api/v1/runs/{id}/knowledge` because a run can retrieve several immutable snapshots.
Submit tasks to `/api/v1/accounts/{id}/runs` with a UUID `idempotency_key` (HTTP 202), then poll
`/api/v1/runs/{id}` and `/events?after_sequence=0`. Exact retries preserve the original response;
reusing a key with another payload returns 409. Version-sensitive actions reject stale versions.
The operator API is a loopback-only synthetic demo and has no remote operator authentication.

## Knowledge service boundary and MCP

`KNOWLEDGE_MODE=demo_fixture` selects the explicit synthetic source. To integrate the friend-owned
knowledge module in this same backend, set `KNOWLEDGE_MODE=in_process` and
`KNOWLEDGE_SERVICE_FACTORY=package.module:factory`. The factory must return the `KnowledgeGateway`
contract in `app/modules/knowledge_context/contracts.py`: bounded `query_approved(request, caller)`
and `validate_references(references, at, caller)`. Results require approved/effective source versions,
citation IDs and hashes. Missing/unavailable/invalid required knowledge blocks generation and dispatch.
The integrated producer implements ingestion, scoped hash-projection retrieval,
independent QA/approval, immutable templates and source retirement in
`app/modules/knowledge/`. Select the factory
`app.integrations.knowledge.trust_adapter:create_gateway` for the in-process mode.
See the [knowledge integration runbook](docs/integrations/knowledge-service.md).

The MCP listener is separate from the local operator REST API. It exposes seven assigned-run tools
through the maintained MCP SDK Streamable HTTP transport. See
[Hermes and MCP runbook](docs/integrations/hermes-mcp.md) for the token, expiry, allowed host/origin,
ngrok, and runtime setup. Bind only the MCP listener to an ngrok tunnel; port 8000 contains local
operator actions. Configure a distinct `MCP_BEARER_TOKEN` and timezone-aware `MCP_TOKEN_EXPIRES_AT`.
A principal must own a current run/account/attempt/fence assignment for execution. Exact duplicate
writes may read the recorded result after their own pause, while changed/stale assignments are denied.

```sh
uv run uvicorn app.mcp.server:create_app --factory --host 127.0.0.1 --port 8002
# Or from Compose, after configuring the MCP credential and expiry:
docker compose --profile mcp up -d --build backend worker mcp
```

For a host Hermes server reached by a worker container, configure its reachable host binding and
`HERMES_API_BASE_URL=http://host.docker.internal:<port>`; localhost inside the container is the
container itself. Provider credentials stay backend-only. Registry output never returns credentials
or configured URLs. `configured` is separate from live health: an unprobed configured dependency has
`health=unknown`. MCP fixture/protocol evidence, simulator evidence, actual Hermes evidence, and actual
knowledge-module evidence must be reported separately.

Every runtime PostgreSQL transaction applies transaction-local `statement_timeout` (10,000ms by
default) and `lock_timeout` (2,000ms); connection establishment is limited to five seconds. Configure
`DATABASE_STATEMENT_TIMEOUT_MS` and `DATABASE_LOCK_TIMEOUT_MS` within 1–60,000ms. These settings preserve
libpq search-path options and reset when the transaction closes. A timed-out sync MCP call waits for
its bounded database operation and rollback to finish; cancellation never abandons a thread that can
write later. These are per-statement/dependency bounds, not a total multi-statement transaction deadline.
## Evidence-control MVP

Erwan's contribution adds approved knowledge retrieval, weighted source dependencies,
source retirement, deterministic safety checks, synthetic dispatch and a hash-chained
audit. Portfolio and trust use PostgreSQL 17 with pgvector. Collection runs consume
the producer through the shared REST/MCP service boundary. The integration runbook
records configuration, evidence contracts and acceptance commands.

### Integrated API

Trust routes are opt-in on the team's main API: set `TRUST_ENABLED=true` and supply
independent `TRUST_JWT_SECRET` and `TRUST_APPROVAL_SECRET` values of at least 32 random
bytes each through the environment. Secrets have no usable defaults. Trust uses `DATABASE_URL`. In-process collection
evidence validation requires trust and collection to use the same PostgreSQL database.
Keep `TRUST_DEMO_AUTH_ENABLED=false` for the integrated backend. Compose enforces that
setting. Provision scoped signed sessions through your trusted identity adapter;
production identity integration is outside this MVP.

Canonical routes use `/api/v1/trust`: knowledge documents/search/graph, templates,
mapped legacy outreach/conversations, and audit events/verification. Pydantic response
schemas appear in OpenAPI. New case creation returns `legacy_case_mapping_required`.
Mapped operations use the canonical collection gate and receipt storage.

### Explicit local demonstration

The demo launcher provides a local identity picker and fresh signing keys in memory.
It binds only to loopback, fixes time to Monday 9 November 2026 at 10:00 WIB and uses
synthetic records. Demo approvals demonstrate the workflow, not actual legal review.

Run on a **fresh migrated database**, or the portfolio database migrated from
`0001_accounts`. The former standalone bootstrap database is not automatically adopted.
For the default Compose database, run from the repository root:

```sh
uv sync --locked
docker compose up -d --wait postgres
export DATABASE_URL=postgresql://collectflow:local-demo-password@127.0.0.1:5432/collectflow
uv run alembic upgrade head
uv run python -m app.trust.demo --serve --port 8001
```

Open [the console](http://127.0.0.1:8001/console) or
[Swagger](http://127.0.0.1:8001/docs). The standalone app retains hidden unversioned
route aliases for earlier demo clients; new callers use `/api/v1/trust`. Restarting
invalidates sessions and dispatch tokens; persisted knowledge approvals remain.
If port 8001 is busy, choose a different `--port`.

Native PostgreSQL 17 with pgvector is also supported. Set `DATABASE_URL` to that DSN
and run Alembic before launching. If using `TRUST_DATABASE_URL`, migrate that exact
DSN by setting `DATABASE_URL` to it for the Alembic command. Startup performs readiness
checks only and never creates tables. `docker-compose.trust.yml` has been superseded
by the shared `compose.yaml` stack.

To verify the synthetic loop without a browser:

```sh
uv run python -m app.trust.demo --report docs/evidence/demo.json
uv run python -m app.trust.benchmark --requests 100
```

Mutating POSTs except demo login require an `Idempotency-Key` UUID. Search always
checks current knowledge state even with a repeated key. Scope comes from the signed
session. List queries are bounded and expose limit/offset pagination. No portfolio
account ID is automatically treated as a borrower identity or collection mandate.

### Limits and handoff

Read [handoff and integration](docs/TRUST_HANDOFF.md),
[scope](docs/TRUST_MVP.md), [security](docs/SECURITY.md) and
[observed verification](docs/09_Implementation_Report.md).
Vectors are a **nonsemantic hash baseline**; Layer 2 is **mocked**. Real semantic
retrieval, live guardrail model quality, full five-agent orchestration and the original
50-persona evaluation are not claimed. Critical evidence expands within a fixed context
budget and verifies hashes. Every dependency remains mandatory regardless of weight.

The global audit lock serializes trust writes. The sequential local benchmark is not
a production load test. Browser permission was declined; no visual QA is claimed.


## Daily collection automation

The collection worker scans overdue accounts with positive outstanding balances from
09.00 Asia/Jakarta each day. Set `SCHEDULER_ENABLED=false` to disable admission and
automatic delivery. `SCHEDULER_START_HOUR` configures the Jakarta start hour, and
`SCHEDULER_RUNTIME_MODE` explicitly selects `simulated` (default) or `hermes`.
Apply the latest migration before starting the existing `app.workers.collection`
process. Compose passes these settings to that worker.

Daily admission and its run/job records commit together. A durable per-account/date
ledger prevents duplicate runs across scheduler replicas and restarts. The manager
coordinates knowledge retrieval, collection and QA through the existing run workflow.
Completed scheduled simulated drafts are delivered automatically through the canonical
backend gate. Receipt, audit, dispatch idempotency and ledger changes commit together.
Manual runs keep their explicit dispatch action. Hermes follow-up remains blocked
while the independent live classifier is unavailable.

The scheduler rechecks eligibility, verified authority, borrower-local contact hours,
calendar coverage, daily/weekly budgets, holds and approved knowledge. Routine calendar
and frequency constraints appear as deferred. Operational failures appear in the
bounded attention summary returned by `GET /api/v1/collection-automation/status`.
Open human reviews use the existing `/api/v1/reviews` contract. Hardship, dispute
and human-request reviews use explicit, versioned operator decisions.

Authority data older than 24 hours blocks contact. Demo seed verification is recorded
once and is not refreshed by scheduling or repeated seed execution. Multi-day demos
require an authoritative synthetic data refresh before collection becomes eligible.
Catch-up scans use the current Jakarta date. An interrupted external runtime requires
reconciliation through the existing run recovery operation.

The overview refreshes automation progress and open-review nudges every 30 seconds.
Open a review to discuss its account with the manager in place. Discussion submits
`collector_brief` with a validated `review_id` to the existing runs API, creates an
operator-owned session, and carries current case facts, review status and minimized
conversation context. A discussion supports advisory artifacts only. Review resolution
and resume use the existing explicit actions and return the backend contact decision.
Scheduled histories are read-visible with `origin=scheduled` and scheduler-authored
turns. Follow-up operator conversations start their own sessions. The portal stores
only discussion IDs, runtime mode and recovery keys across refresh.

## Portfolio Manager chat

The portal `/manager` is a persistent conversation across the synthetic portfolio.
Account drill-down supplies an explicit `context_account_id` for the next turn.
Advanced account tasks remain available at `/manager/tasks`.

`POST /api/v1/manager/chat/runs` accepts an instruction, explicit runtime mode,
UUID idempotency key, optional manager session and optional account context.
It returns the existing queued-run contract. Portfolio runs have `account_id=null`,
`scope=portfolio` and task type `manager_chat`. Existing account task inputs keep
their three task types. Global sessions and direct session reads are under
`/api/v1/manager/chat/sessions`. Lost acceptance uses the read-only
`/api/v1/manager/chat/requests/{key}` endpoint. Messages, events, cancellation and
permitted reconciliation reuse the existing manager/run endpoints.

Portfolio MCP tools read summaries, search accounts, inspect cases, save a Manager
response and propose account tasks. Each validates the active portfolio run,
principal, attempt, fence and lease. An operator executes a proposed task through
the existing account run API, using the saved proposal artifact UUID as the replay
key. The worker requires the runtime final summary to match a persisted Manager
response artifact.

Simulated chat displays a labeled snapshot of current backend facts and account
task cards. Free-form reasoning uses Hermes with its configured MCP connection.
After updating the backend tool catalog, update the profile's tool allowlist and
reload its MCP connection. The setup and twelve tool names are documented in
`docs/integrations/hermes-mcp.md`. A healthy Runs API alone does not verify the
profile's current tool catalog.

### Public regulatory sources

The [source register](docs/REGULATORY_SOURCES.md) identifies 15 original PDFs and
61 selected provisions, including amendments, court holdings and official holiday
decrees. The shared Gemini research was checked against these originals; it is not
itself a legal source. Original Indonesian text is preserved without machine rewriting.

The latest head, `0010_gmail_collection_demo`, includes the source archive and
collection channel migrations while preserving published histories. Apply it to the intended
PostgreSQL database, then run:

```sh
uv sync --locked
uv run alembic upgrade head
uv run python -m app.modules.knowledge_sources.ingest --use-cache
```

For teammate handoff, the public PDF/extraction bundle is included in
`deployment/graceline-kb-public-sources.tar.gz` at Erwan's explicit request.
With `DATABASE_URL` set to the intended local PostgreSQL 17 + pgvector database,
run `bash scripts/import-kb-demo.sh`. Back up an existing target database first.
The script checks the bundle hash, applies migrations, and stages all 15 sources
as draft/review-required. It does not import borrower records or credentials and
does not approve regulations. Re-running ingestion is idempotent.

The CLI uses `DATABASE_URL` (or `TRUST_DATABASE_URL`) from the process environment,
never dotenv. `--use-cache` accepts only byte/hash-pinned files under
`data/knowledge_sources/`; otherwise it downloads from the configured HTTPS host
allowlist. Omit this flag to re-fetch originals. Restrict a run with
`--source pojk22_2023`, or use `--manifest path/to/curator-controlled.json`.
Changed PDF bytes fail the manifest pin and require a new source review.

Only the explicitly requested public-source handoff bundle is included in Git;
working PDFs, extraction sidecars and database backups remain ignored. The curated sidecars
preserve exact reviewed page segmentation. A fresh checkout uses pypdf when they
are absent; a different extraction remains review-required, and scanned/malformed
content cannot silently become usable knowledge. Publisher availability or extraction
failure is reported per source, with a nonzero exit if any source fails. OCR is not
automatically performed by this CLI.

PDF bytes are stored once in PostgreSQL. Scope-specific source records preserve the
manifest and extraction snapshot; selected passages map to draft KMS documents or
quarantine reasons. Re-importing the same manifest is idempotent. Correcting extraction
or metadata appends a new snapshot and preserves history. No archive operation changes
the scheduler, calendar, recipient checks, tool permissions or dispatch gate.

Reviewer endpoints (authenticated `knowledge`, `qa`, `compliance`, `admin` only):

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/v1/trust/knowledge/sources?limit=25&offset=0` | Scoped, bounded inventory |
| GET | `/api/v1/trust/knowledge/sources/{source_id}` | Dates, provenance, provision mappings and quarantine reasons |
| GET | `/api/v1/trust/knowledge/sources/{source_id}/download` | Original PDF attachment, SHA-256 header and audited access |

All imported passages remain `draft` and `review_required`, with reviewer-only access.
They cannot pass QA or enter normal agent retrieval until a reviewed derivative/version
is explicitly ingested and independently approved through the existing workflow.
No API can turn downloaded text into executable rules. A legal status such as
`in_force` is curated source metadata, not a system-issued legal opinion.

Before migrating a populated database, take a consistent `pg_dump` backup and test
restore to a separate database. The archive migration refuses a downgrade once it
contains data; use a forward migration or reviewed recovery plan. Source files are
public regulatory records, not borrower transcripts. Private operational data remains
outside this archive and outside the KMS.
