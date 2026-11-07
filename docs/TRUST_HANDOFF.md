# Trust contribution handoff

The branch integrates main through `b517c53`, preserving portfolio routes and all
existing safety tests. Commit and push are reserved for Erwan. No real borrower or
external-channel integration is included.

## Entry points

- `app/main.py`: portfolio plus opt-in `/api/v1/trust` API.
- `app/modules/trust`: typed router/schemas, service transaction owner, request repository.
- `app/trust`: evidence lifecycle, deterministic guardrails, screening and data processing.
- `app/services/dispatch_gate.py`: only module permitted to import simulated channels.
- `app/config.py` and `app/db/connection.py`: shared configuration/connections.
- Alembic `0001_accounts -> 0002_trust`: shared schema history, pgvector required.

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
- No live model, MCP transport, full agents, original 50 personas or browser E2E claim.
- Docker/CI execution and production identity/retention/restore remain unverified here.

See `09_Implementation_Report.md` for the checks actually run on the final branch.
