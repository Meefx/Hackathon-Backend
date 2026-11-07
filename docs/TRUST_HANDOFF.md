# Trust contribution handoff

The branch integrates main through `2f33388`, preserving portfolio routes and all
existing safety tests. Commit and push are reserved for Erwan. No real borrower or
external-channel integration is included.

## Entry points

- `app/main.py`: portfolio plus opt-in `/api/v1/trust` API.
- `app/modules/trust`: typed router/schemas, service transaction owner, request repository.
- `app/modules/knowledge`, `compliance`, `conversations`: canonical evidence, policy
  and collection domains; `app/trust` retains compatibility facades and demo wiring.
- `app/modules/conversations/dispatch.py`: sole delivery boundary;
  `app/services/dispatch_gate.py` is its compatibility adapter.
- `app/config.py` and `app/db/connection.py`: shared configuration/connections.
- Alembic head `0006_source_collection_merge` joins `0003_source_archive` and
  `0005_knowledge_collection`; published histories remain unchanged, pgvector required.
- `app/modules/knowledge_sources`: curator CLI, immutable source archive and scoped reviewer API.
- `app/policies/corpus`: 15 pinned public sources with article/section and amendment provenance.

## Team integration contracts

1. KMS access requires an authenticated Actor with a valid principal/product/role.
   Do not accept identity or approval claims directly from agent arguments.
2. Map planned `collection` and `improvement` names explicitly to existing `personal`
   and `process` roles at a reviewed adapter boundary. No privilege aliases exist yet.
3. `get_approved_sop` must call the scoped, in-force retrieval service.
   `simulate_dispatch` must use the existing outreach pipeline and final gate.
4. Account IDs such as `CF-1001` are not mandate or recipient proof. Trust cases have
   explicit UUID identities; mapping requires a reviewed account/authority contract.
5. The trust audit is a global hash chain. The planned per-account audit should not
   become a second source of truth without explicit reconciliation.
6. Hard rules remain executable code/config; retrieved text and dependency weight
   cannot override eligibility or permissions. Conversation content stays outside KMS.

## Safe setup

Use README commands with PostgreSQL17 + pgvector. Apply Alembic explicitly before
startup. The old pre-Alembic demo DB is preserved and is deliberately rejected by
migration; use a fresh migrated demo DB or prepare a reviewed data migration.
Never `stamp head` to bypass that check. Downgrade refuses populated trust data.

Tests require `TEST_DATABASE_URL` ending `_test` and CREATEDB privilege. Each test
creates and removes only its own random database. Legacy `TRUST_TEST_DATABASE_URL`
is accepted as a fallback but must also end `_test`. Do not use the demo database.

## Deliberate limits

- Synthetic borrowers and simulated DB delivery only. English UI/API/demo copy.
- Hash-projection vectors are nonsemantic; Layer 2 is a mock.
- Source QA/human records prove workflow participation and artifact binding, not law.
- Global write serialization favors the small demo; production throughput is unmeasured.
- Existing trust primitives still combine domain logic and SQL. Further extraction into
  repositories is gradual; do not duplicate or replace the gate during extraction.
- The team's MCP/worker implementation is included from main. Live-model quality,
  original 50-persona evaluation and browser E2E are not claimed by this contribution.
- Local backup restore was verified before the source migration; production restore,
  Docker/CI execution, production identity and retention remain unverified.

## Regulatory corpus handoff

Run the README ingestion CLI against the migrated database. All staged legal excerpts
are draft/review-required, never automatically approved. Original PDF and extraction
hashes are distinct. Reviewer downloads include `X-Content-SHA256`; borrower-facing
agents cannot access archives. Do not grant an agent a generic fetch/download tool.

Resolve source context, amendments, lender applicability and extraction defects before
publishing a separately reviewed version through the existing KMS QA/human workflow.
The source register records corrections to the Gemini research. No research claim
relaxes a hard guardrail. Retain the original Indonesian legal text; English summaries
are separately reviewed derivatives, not authoritative translations.

See `09_Implementation_Report.md` for the checks actually run on the final branch.
