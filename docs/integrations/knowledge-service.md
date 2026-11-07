# Knowledge service and collection integration

Collection runs consume approved KMS sources through the in-process knowledge
gateway. Document publication requires independent QA and compliance approval.
Retirement suspends dependent templates and blocks dispatch using retired evidence.

## Ownership

| Module | Responsibility |
| --- | --- |
| `app/modules/knowledge/` | Documents, chunks, approval, templates, retrieval and KMS audit |
| `app/modules/knowledge_context/` | Immutable run snapshots, citations and evidence hashes |
| `app/integrations/knowledge/` | Gateway selection, corpus scope and producer mapping |
| `app/modules/compliance/` | Contact policy, rendering, screening and approval binding |
| `app/modules/conversations/dispatch.py` | Operator dispatch and the channel boundary |
| `app/modules/conversations/compatibility.py` | Explicit legacy case-to-account bridge |
| `app/modules/audit/` | Account run, contact, review and dispatch audit |

Compatibility modules under `app/trust/` delegate to these domains. Historical trust
records retain their IDs and audit hashes. Mapped operational effects use collection
messages, approvals and receipts.

## Setup

Alembic joins `0002_trust` and `0003_review_holds` at
`0004_collection_trust_merge`. The schema head is `0005_knowledge_collection`.
Historical snapshots retain their version-one hash algorithm.

PostgreSQL 17 requires pgvector. Compose and CI use `pgvector/pgvector:pg17`.
For an existing database, take a consistent backup and verify the upgrade on a
disposable copy before replacing its PostgreSQL image or applying migrations.

```sh
uv sync --locked
docker compose up -d --wait postgres
uv run alembic upgrade head
uv run python -m app.seed
```

Seed explicitly provisions fictional account, recipient, corpus and borrower
authority. Its calendar is a synthetic demo fixture. Existing authority decisions
and first-seeded account dates are preserved.

Select the producer in the API and worker environment.

```dotenv
KNOWLEDGE_MODE=in_process
KNOWLEDGE_SERVICE_FACTORY=app.integrations.knowledge.trust_adapter:create_gateway
```

```sh
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run python -m app.workers.collection
```

Publish approved sources through authenticated trust knowledge routes. Set
`TRUST_ENABLED=true` and supply independent `TRUST_JWT_SECRET` and
`TRUST_APPROVAL_SECRET` values through backend environment configuration. The
integrated API keeps its demo identity picker disabled.

The standalone demonstration provides explicit synthetic identities, fresh signing
keys and local fixture provisioning. It uses Monday 9 November 2026 at 10.00 WIB.

```sh
uv run python -m app.trust.demo --report /tmp/collectflow-trust-demo.json
```

The demonstration provisions fictional legacy mappings, exercises the approved
template through the collection gate, retires the source and verifies the audit.

## Contracts

`GET /api/v1/runs/{id}/knowledge` returns bounded pages using
`{items,total,limit,offset}`. MCP retrieval exposes matching evidence through its
structured output schema. New snapshots use `snapshot_hash_version=2`, binding
sources, critical context and the retrieval profile. Historical version-one
snapshots retain their original hash and citation identity.

Source `content_hash` uses the collection canonical JSON-string digest.
`document_hash` and `snippet_hash` preserve raw UTF-8 SHA-256 provenance. Primary
results contain at most five snippets, each limited to 4,000 characters. Critical
context retains up to 64 chunks and 24,000 characters. Larger sources require review.
The portal shows synthetic URIs as text provenance and loads evidence pages
explicitly. Loaded citations survive same-run version updates.

Corpus and evidence role come from backend authority and active assignment. The
synthetic operator is `local-operator`. Assigned worker and MCP principals use the
producer's `personal` evidence-read role. Agent history is redacted by the collection
service facade. Ingestion, independent QA and approval use separate capabilities.

`POST /api/v1/trust/cases` returns HTTP 409 with
`legacy_case_mapping_required`. Legacy outreach requires a persisted mapping with
matching corpus and borrower identity. Historical delivered attempts preserve their
identity on replay. Existing contact history and holds participate in the gate.

## Dispatch and audit

Dispatch locks KMS governance and evidence before account, borrower budget, message
and idempotency records. After blocking locks, it reads the clock and validates
source/template status, recipient, consent flags, eligibility, holds, calendar,
borrower timezone, freshness, frequency, exact text and approval expiry. Receipt,
idempotency response and account audit commit together.

Evidence and collection use the same PostgreSQL database. A mutable gateway without
transaction-bound reference validation blocks dispatch. Retrieval deadlines bound
database work and lock waits. The installed PostgreSQL driver has a two-second minimum connection timeout, including requests with a shorter remaining connection budget. The receipt transaction uses the local simulated channel.

KMS audit owns document, template and retrieval events. Account audit owns run,
review and contact effects. Dispatch correlation points to the canonical message,
whose binding links snapshots, template and source IDs. Both chains preserve their
history and have independent verification.

## Verification

The dedicated test role requires CREATEDB. Each test uses a disposable database to
isolate the fixed `trust` schema.

```sh
uv sync --locked
uv run ruff check .
uv run mypy app
uv run alembic heads
docker compose --profile test up -d --wait postgres-test
TEST_DATABASE_URL=postgresql://collectflow_test:local-test-password@127.0.0.1:5433/collectflow_test uv run pytest -q
```

The frontend includes fixture tests and an opt-in real KMS browser journey. Prepare
an approved synthetic critical source, an eligible account and a worker using the
in-process producer. Supply `KNOWLEDGE_MODULE_SMOKE=1`, `KNOWLEDGE_DOCUMENT_ID`,
`KNOWLEDGE_ACCOUNT_ID`, `TRUST_API_TOKEN` and `NEXT_PUBLIC_API_URL` to the test process.
The retirement token stays in Node and tracing is disabled for that test.

```sh
npm run test:smoke -- --grep knowledge_module
```

Hash projection retrieval and simulated classification expose their demo profiles.
Semantic retrieval, live classifier quality and Hermes runtime acceptance follow
the separate requirements in B6 of the backend plan.
