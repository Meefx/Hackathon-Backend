# Data trust MVP finalization report

Observed on 7 November 2026 (WIB). Branch: `codex/erwan-kms-guardrails-security`.
Main integrated through `b517c53` on top of the earlier trust commit `ae98dbd`.
Merge is prepared without a commit. Erwan will commit and push.

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
