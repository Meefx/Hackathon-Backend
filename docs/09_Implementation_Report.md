# Data trust MVP finalization report

Observed on 7 November 2026 (WIB). Branch: `erwan-kms-guardrails-security`.
The earlier integration is committed at `77cc1b4`; regulatory sources at `253135a`.
The current reconciliation with main `2f33388` is staged; Erwan will commit and push.
Sections 1–6 record prior work. Section 7 records the current reconciliation.

## 1. Result

The trust contribution now integrates with the team's PostgreSQL/Alembic backend.
Portfolio contracts are preserved. Opt-in `/api/v1/trust` routes expose the same
approved knowledge, evidence retirement, safety pipeline and simulated dispatch as
the standalone demo. No second dispatch path exists. Everything remains synthetic.

The verified loop is: ingest, separate QA and demo human approval, scoped retrieval,
approved dependent template, screened outreach, source retirement, automatic template
suspension, next outreach denied, audit verified. Canonical API and CLI both passed.
An English console is provided; browser permission was declined earlier and visual
inspection/browser E2E are not claimed. Existing browser server was not restarted.

## 2. Finalization changes

- Integrated main's engineering standards and PostgreSQL foundation without discarding
  either branch's tests. Resolved all seven overlapping merge paths.
- Alembic `0001_accounts -> 0002_trust` owns all DDL. Frozen SQL assets live with the
  revision. Startup only checks readiness. Upgrading preserves existing account rows.
- Unversioned trust schemas are rejected unchanged. Populated trust downgrade is
  refused. The old bootstrap demo database was not modified or adopted.
- Settings and connections are centralized. Compose/CI use a pgvector image.
  Trust is opt-in with no default signing keys; Compose disables demo login.
- Added module router, explicit input/output/error schemas, transaction service and
  request repository. Existing safety/evidence primitives are retained during gradual
  extraction; there is no wholesale duplicate implementation.
- Sync DB calls stay in sync handlers; pool startup/shutdown are offloaded and cleaned
  up on failure. Lists are bounded; retrieval reads time after waiting for its lock.
- Dispatch verifies caller capability before any delivery or idempotent replay.
- Fixed-schema DB tests use random disposable databases on a dedicated `_test` base.
  No tests point at the demo database; test role requires CREATEDB.
- Updated README, scope, decisions, security context and `TRUST_HANDOFF.md`.

## 3. Commands and observed outputs

| Command/check | Observed result |
|---|---|
| `git pull --ff-only origin main` | Fetched current main; could not fast-forward because histories diverged |
| `git merge --no-commit --no-ff origin/main` | Prepared merge; conflicts resolved locally, no commit |
| `uv lock && uv sync --locked` | Resolved 45 packages; Alembic/SQLAlchemy dependencies installed |
| Full `TEST_DATABASE_URL=... uv run pytest -q` | `358 passed, 1 warning in 49.79s` |
| Final API-schema follow-up | `17 passed, 1 warning in 8.94s` |
| Safety/config targeted checks after fixture update | `175 passed in 0.32s` |
| Architectural import/clock follow-up | `2 passed, 1 warning in 0.71s` |
| `uv run ruff check .` | `All checks passed!` |
| `uv run mypy app` | `Success: no issues found in 40 source files` |
| `uv run alembic heads` | `0002_trust (head)` |
| Ruff format check over contribution Python files | `42 files already formatted` |
| Staged forbidden-path, selected credential and conflict-marker scan | `PASS`, 59 text files checked |
| `git diff --cached --check` and unstaged diff | Exit 0, no output; all final changes staged |
| `npx --yes prettier --write app/trust/console.html` | `app/trust/console.html 217ms (unchanged)` |
| Explicit CLI migration then `python -m app.trust.demo --report ...` | Exit 0; first `DELIVERED`, next `NOT_SENT`; duplicate same attempt; retired source absent; audit valid, 18 events checked |

Environment: macOS, uv, Python3.12, native PostgreSQL17.11 + pgvector0.8.7 on localhost
port55432. Tests use `collectflow_test` only as a base for randomly named databases.
CLI verification uses a new `graceline_trust_final_demo` database. Generated demo
reports are ignored by Git. No keys, token contents or borrower messages are logged.

The one warning is the existing Starlette/httpx TestClient deprecation. Docker is
unavailable on this machine; Compose was updated but not executed. GitHub Actions
was not run remotely. Dependency/network and localhost test commands used approved
sandbox escalation after ordinary sandbox access failed.

## 4. Safety evidence and test adjustment

All eight original invariants remain covered: dispatch-time eligibility, borrower-only
verified recipients, exact-text HMAC, sole channel importer, fail-closed screening,
at-most-once delivery, append-only audit, and holds for escalated/disputed cases.
Actual separate PostgreSQL transactions test concurrent duplicate dispatch and source
retirement races. Audit-write failure rolls back simulated delivery. New tests cover
schema/data preservation, refusal of destructive downgrade, isolation, canonical API
parity, permissions before replay, bounded lists and expiry across lock waits.

One old lock-wait unit test used an incomplete `SimpleNamespace` actor. The new entry
permission check correctly rejected it before the clock assertion. Its fixture now
uses a valid scoped operator Actor so it continues testing closing-time revalidation.
Its `NOT_SENT`, `OUTSIDE_HOURS` and zero-delivery assertions are unchanged. Ten new
regressions independently assert unauthorized roles cannot dispatch or replay.
The injected-clock architectural check now includes the new trust module directory.
No safety assertion, Layer 1 pattern or fail-closed path was removed or weakened.

## 5. Product scope and limitations

This completes the data/safety contribution for handoff, not all original docs/08
mandatory scope. Implemented baseline IDs include FR-GEN-02/03..05/08..10,
FR-SCREEN-01..06/09, FR-SCHED, FR-RCPT, FR-WF-04..07, FR-AUD-01..04/07 and partial
FR-RBAC/FR-HARD integration. KMS approval, evidence dependencies and critical context
verification are extensions; no original PR is broadly claimed fully Done here.

- Vectors are explicitly local hash projections, not semantic embeddings. Layer 2 is
  mocked. No live-model quality or semantic retrieval claim follows from these tests.
- Evidence weights request deeper context, never override law, access or hard rules.
  Critical context is bounded at 24,000 characters / 64 chunks with hash verification.
- QA/human approval records prove workflow and artifact binding, not legal correctness.
  Supplied ZIP research remains review-required; no retrieved text becomes executable law.
- English UI/API/synthetic borrower copy follows the user's override. Unknown timezone
  denies. Escalated/disputed cases remain denied for every purpose, the stricter rule.
- Conversations stay outside KMS, with restricted raw read and redacted model context.
  Detection patterns cannot guarantee removal of all PII or prompt injection.
- The audit lock serializes trust writes. The earlier local benchmark (p50 8.9ms,
  p95 13.61ms, 100 sequential requests) predates this integration and is not a new
  result, load/availability test or production latency claim.
- Demo identities are not production authentication. DB-owner tampering, production
  retention/rights, encryption, least-privilege roles, restore and live-provider
  reconciliation still need review before any pilot.
- Full five-agent orchestration, MCP transport, original review queue/screens,
  scheduler worker, replay/export, original 50-persona evaluation and browser E2E
  remain outside this contribution. No real channels, audio or payment handling.

Use README for commands and `TRUST_HANDOFF.md` for the team integration boundaries.

## 6. Regulatory source ingestion, 7 November 2026

Pulled `origin main` before this task and again during finalization: both returned
`Already up to date.` No commit or push was performed. Gemini browser access was
declined; the user supplied the research text directly. Its claims were checked
against official originals and were never treated as implementation instructions.
`REGULATORY_SOURCES.md` records exact URLs, outdated claims, amendments and limits.

Implemented a curator-only HTTPS downloader (host allowlist, public-IP/DNS pinning,
redirect validation, MIME/magic, time/byte limits), deterministic PDF extraction and
manifest-driven article/section selection. Original PDF and extracted page hashes
are separate. Sidecars are pinned; OCR and faulty text layers remain review-required.
Fresh ingestion of a pinned scan without OCR archives its bytes and quarantines its
empty provisions. No model performs rewriting, legal approval or policy compilation.

Migration `0003_source_archive` adds immutable `source_artifacts`, scoped extraction
snapshots in `source_records`, and `source_provisions` mappings to drafts/quarantine.
New snapshots preserve history; identical imports replay without duplicate writes.
Download endpoints validate byte integrity and audit access before returning content.
Only knowledge/QA/compliance/admin reviewer roles can access archives. Models have
no new fetch/download tool. The existing dispatch path and hard policy are unchanged.

The local `graceline_trust_final_demo` database contains 15 original PDFs, 788 physical
pages and 19,973,008 bytes. The current manifest defines 61 selected provisions across
two authorized scopes. The initial run created 25 scoped snapshots, 96 review-required
KMS drafts and five quarantined provision mappings, with zero source failures and
zero automatic approvals. Replaying it reported `replayed: 25`, without extra writes.
Subsequent corrections to court reasoning and a malformed article boundary append
new draft snapshots; historical drafts remain immutable and unavailable to agents.

Before migration, a consistent custom-format `pg_dump` was restored to a disposable
database. All 13 existing tables matched row counts and content fingerprints. After
migration, unaffected operational tables still match the backup. API verification
downloaded all 15 originals through 25 scoped records, verified every SHA-256, denied
the personal role, and verified the audit chain (165 events at that checkpoint).
No external source is approved. The old pre-Alembic database remains untouched.

Tests exercise actual PostgreSQL migrations, scope/role isolation, original downloads,
byte tampering, audit outage, immutable update/delete/truncate, idempotency, revised
extractions, malformed/currentness metadata, SSRF and scanned/PII quarantine. The
OpenAPI assertion now explicitly accepts the documented binary PDF response; its
JSON assertions for every other route and existing safety assertions are retained.

Observed initial complete suite: `486 passed, 1 warning in 84.60s (0:01:24)`.
Ruff: `All checks passed!`. Mypy: `Success: no issues found in 50 source files`.
The warning is the existing Starlette/httpx TestClient deprecation. Updated final
verification is appended below after the corpus-boundary and scan regressions.

Live downloader check succeeded for the official 2026 holiday PDF (1,056,688 bytes,
matching its pinned hash). A separate live fetch of peraturan.go.id failed with a
read error and reported `SOURCE_FETCH_FAILED`; the already downloaded hash-pinned
official PDF was used for ingestion. Publisher availability is not guaranteed.
No stale source is silently accepted after a hash mismatch or retrieval failure.

Remaining boundaries: independent source/legal review before publication; approved
English derivatives for English-language semantic use; lender mandates/SOPs and
current AFPI editions; production auth, encryption, retention and parser isolation.
Local hash vectors and mock Layer 2 are unchanged. No claim of exhaustive current
law, live collections, full five-agent orchestration or production latency is made.

Final checks: complete expanded suite `533 passed, 1 warning in 74.68s (0:01:14)`;
after the last manifest-boundary correction, `25 passed in 0.27s` for the complete
manifest suite. `uv sync --locked`: `Resolved 46 packages`, `Checked 43 packages`.
Ruff passed, mypy passed for 50 source files, formatter reported `22 files already
formatted`, and Alembic reported `0003_source_archive (head)`.
Corrected manifest reimport: 19 existing snapshots replayed and six corrected scoped
snapshots appended. Current-manifest totals remain 96 drafts and five quarantine
mappings; history contains 31 snapshots, 138 drafts and seven quarantine mappings.
Final audit verification: valid, 213 events. External approved knowledge count: zero.

## 7. Reconciliation with updated main before the next PR

Main advanced to `2f33388` after the source contribution was committed and pushed
as `253135a`. Pulling with `--ff-only` correctly refused the diverged history. A
noncommitting merge integrates that exact main revision into the existing Erwan
branch; commit and push remain with Erwan.

Resolved conflicts in database readiness, authorization compatibility imports and
dependencies. Retained the team's canonical knowledge producer, collection/worker/MCP
integration, and both `mcp` and `pypdf` dependencies. Source reviewer capabilities
now belong to the canonical authorization module, with the legacy facade re-exporting
the same Actor and permission checks. No agent download tool was added.

Published migration histories remain unchanged. New no-op revision
`0006_source_collection_merge` joins `0003_source_archive` and
`0005_knowledge_collection`. Readiness checks both collection and source tables.
Tests upgrade populated databases from each published head and compare all prior
table contents, then verify idempotent upgrade and the single resulting head.
Two existing migration assertions now reference `REQUIRED_REVISION`, preserving
their single-head and no-data-loss checks while accepting the new head.

Initial integrated suite exposed a pre-existing timezone-dependent approval failure:
an expiry signed in UTC was read from PostgreSQL in the session timezone and produced
a different HMAC binding despite representing the same instant. `binding_for` now
serializes aware expiries as UTC and rejects missing timezone information. Signature,
content/evidence binding, expiry and fail-closed checks remain intact. Regression tests
cover five timezones and ensure changing the actual expiry changes the signature.
No persisted approval is rewritten or automatically renewed.

The same timezone round-trip issue affected the team's per-account audit verifier.
New audit timestamps and verification now use canonical UTC; missing/invalid timezone
data fails verification. Database round-trip tests cover UTC, Jakarta and New York,
and retain changed-time/outcome tampering rejection. Existing audit rows/hashes are
never rewritten. Previously stored non-UTC timestamp representations may require
separate historical-format investigation; no fallback accepts an unverifiable chain.
The original `trust.audit_events` serializer already used UTC and is unchanged.

Final observed checks: `692 passed, 1 warning in 143.27s (0:02:23)` for the full
PostgreSQL/MCP/integration suite; `All checks passed!` from Ruff;
`Success: no issues found in 121 source files` from mypy; `11 files already
formatted`; Alembic `0006_source_collection_merge (head)`. The warning is the existing
Starlette/httpx TestClient deprecation. Git reports no unresolved conflicts, no
unstaged changes, and clean diff checks. All merge work is staged, with HEAD still
`253135a` and MERGE_HEAD `2f33388`; no commit or push was performed by the agent.
Existing application databases were not migrated during this PR reconciliation;
upgrade tests used disposable databases containing synthetic data.
