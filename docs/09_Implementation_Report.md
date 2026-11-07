# Data trust MVP implementation report

Observed on 7 November 2026, approximately 10:40 to 11:30 WIB.
Branch: `codex/erwan-kms-guardrails-security`. Local work only; no push.

## 1. Summary

The standalone data/safety contribution works through its API on synthetic data:
ingest source, independent QA, demo human approval, scoped retrieval, approved
dependent template, screened simulated outreach, source retirement, automatic
template suspension, next outreach denied, audit verified. Approval tokens remain
in memory. The team's existing portfolio endpoints and SQLite store are unchanged.
An English console is provided. Browser access to localhost was declined, so visual
inspection and browser E2E are not claimed. Voice and real channels are absent.

## 2. Environment actually used

- macOS, uv 0.11.19; uv selected Python 3.12.13 for the project.
- Native PostgreSQL 17.11 (Homebrew), pgvector 0.8.7, isolated local port 55432.
- Docker is not installed. The supplied Compose configuration was not executed.
- FastAPI 0.162.0, Psycopg 3.3.8, PyJWT 2.15.1; exact packages in uv.lock.
- No API keys or secret files were read or edited. Demo signing keys are generated
  in memory and never printed. All classifier decisions use the declared mock.

## 3. Implemented scope

| Area | Result and evidence | Related baseline IDs |
|---|---|---|
| Knowledge lifecycle | Draft, QA, human approval, exact hashes, effective dates, retirement; tests/test_knowledge.py | FR-GEN-09, FR-SCREEN-09; KMS extension |
| Retrieval | PostgreSQL FTS + pgvector reciprocal-rank fusion; principal/product/role/approved/in-force filters before ranking; retrieval IDs audited | FR-GEN-02, FR-AUD-07; KMS extension |
| Critical evidence | Expand whole source within 24,000 chars / 64 chunks; verify source/chunk hashes; stop for review if over budget | New bounded verification extension |
| Weighted graph | Immutable source-to-template dependency edges with bounded weights; retirement suspends dependents regardless of weight | New evidence-dependency extension |
| Access and processing | Signed scoped JWT, explicit capabilities/tool-purpose allowlist, structural chunking, detected PII/secrets/injection rejected, sanitized conversation context | FR-RBAC, FR-GEN-02/08/10 |
| Safety | Deterministic eligibility, borrower-only simulated recipient, freshness, placeholders/actions/numbers, English/Indonesian Layer1, strict mock Layer2 combination | FR-SCHED, FR-RCPT, FR-GEN-03..05, FR-SCREEN-01..06 |
| Dispatch and audit | Current evidence, text/policy/token/state revalidation, borrower budget, idempotency, atomic simulated inbox/audit, append-only chain | FR-WF-04..07, FR-AUD-01..04/07 |
| Operational conversation | Separate storage, restricted raw read, redacted context, deterministic trigger holds; no automatic KMS promotion | FR-HARD-04/05, FR-RBAC-04; partial integration |

This is not completion of every PR in the original docs/08 section D. The full
Personal/Manager/Process/QA/Knowledge product, scheduler worker, original review
queue, all original case screens, replay/export and original 50-persona evaluation
remain integration work. No original PR is broadly claimed Done by this report.

## 4. Commands and observed output

| Command | Observed summary |
|---|---|
| `git pull --ff-only origin main` before implementation | `Already up to date.` |
| `uv run pytest -q tests/test_store.py tests/test_api.py` baseline | `8 passed, 1 warning, 7 subtests passed in 2.31s` |
| `TRUST_TEST_DATABASE_URL=... uv run pytest -q` final | `277 passed, 1 warning, 7 subtests passed in 4.83s` |
| `uv run ruff check . --output-format concise` final | `All checks passed!` |
| `uv run ruff format --check` over new Python files | `27 files already formatted` |
| `uv run mypy app` | `Success: no issues found in 27 source files` |
| `npx --yes prettier --write app/trust/console.html` | `app/trust/console.html 231ms` |
| `git diff --check` | Exit 0, no output |
| Staged sensitive-pattern check | `PASS`, 43 staged files scanned; forbidden secret-file paths and selected key/phone patterns checked |
| `uv run python -m app.trust.demo --report docs/evidence/demo.json` | Exit 0; retrieved before=true, after=false; DELIVERED then NOT_SENT; duplicate same attempt=true; audit valid=true, 60 events checked for that run |
| `uv run python -m app.trust.benchmark --requests 100` final | Local sequential search+audit: p50 8.9ms, p95 13.61ms, max 16.85ms; 5 warmups, 100 measured requests |

The benchmark uses TestClient and an existing small synthetic database. It excludes
live inference, external network, production load and availability. It does not
establish a production latency SLO. Tests emit one Starlette/httpx deprecation
warning. GitHub Actions configuration was updated, but remote CI was not run.

## 5. Invariants and review evidence

| Invariant | Passing evidence |
|---|---|
| INV-1 | test_inv_gate_revalidates; lock-wait crossing-close regression |
| INV-2 | test_contact_denials; gate recipient revalidation |
| INV-3 | HMAC roundtrip/tampering/expiry cases; persisted hash/policy binding |
| INV-4 | test_inv_only_gate_imports_channels, also restricts token issuer |
| INV-5 | Invalid/unavailable Layer2 cases; test_inv_guardrail_outage_never_allows |
| INV-6 | test_inv_concurrent_gate_delivers_once; idempotency cases |
| INV-7 | test_inv_append_only rejects UPDATE, DELETE and TRUNCATE |
| INV-8 | Escalation denial for every purpose; conversation-trigger hold |

Actual separate PostgreSQL transactions race source retirement against dispatch.
The audit ordering proves a delivery, if any, precedes retirement; otherwise the
gate returns NOT_SENT. A failed post-delivery audit insert rolls the synthetic
delivery back. These claims apply to the simulated database channel only.

Independent review found and reproduced a stale clock after lock waits, malformed
Layer2 categories raising exceptions, evidence lock upgrades, missing persisted
hash/policy binding, and authorization before idempotent replay. The code was fixed
and regression tests added. No safety test or seed pattern was removed or weakened.
An unused JWT-test variable was renamed, Ruff combined equivalent context managers,
and a synthetic phone fixture was composed at runtime to avoid committing a full
phone-like value. A synthetic private-key delimiter is likewise composed at runtime
so the staged sensitive-pattern check can distinguish fixture code. These changes
preserve the exact runtime test inputs and assertions.

## 6. Deviations and limits

- This branch adds `app/trust` beside the team's existing app layout, not a second
  nested `backend/`. It preserves current account APIs and isolates PostgreSQL work.
- Native PostgreSQL substitutes for unavailable Docker. SQL initialization is
  idempotent; an Alembic upgrade/downgrade cycle was not implemented or run here.
- Psycopg uses explicit transactions rather than introducing SQLAlchemy for this module.
- English copy follows the user's explicit override. Unknown timezone denies,
  following the stricter supplied research. Escalation/opt-out acknowledgement
  exceptions remain unresolved and are not enabled.
- Supplied ZIP claims about newer rules remain review-required. No automatic
  translation of retrieved legal text into executable rules occurs.
- Local hash vectors are explicitly nonsemantic. Critical context expansion and
  integrity checks do not prove legal interpretation. Live classifier, generation
  and semantic embedding quality were not run or verified.
- Weights never override legal authority, access, expiry or a hard-rule denial.
  This is a direct evidence dependency graph, not a general graph reasoner.
- Global audit locking serializes modifying trust transactions. This is deliberate
  for the small MVP; multi-writer production throughput and HA are not claimed.
- Demo identity selection and scripted approval roles are not real authentication,
  legal clearance or independent professional review. Audit is tamper-evident;
  the database owner can disable triggers. No production retention/rights,
  processor, cross-border transfer, real recording or pilot approval is implied.

## 7. Remaining work

1. Team integration of auth, principal/mandate records and the operational pipeline.
2. Reviewed source corpus and approved legal interpretations; settle noted source conflicts.
3. Approved semantic embedding and live Layer2 provider; quality evaluation and timeouts.
4. Original 50 personas, UI/browser E2E, review queue integration, replay/export.
5. Production identity, least-privilege DB role, retention/rights procedures, restore,
   provider reconciliation and representative load/fault testing before any pilot.

Zero observed failures in this regression suite is not proof of zero real-world
risk. The original persona M-01..M-13 metrics and production impact are not measured.

## 8. Demo

Follow README's standalone trust section. Open `/console` on local port 8001. Click
load/QA, explicit demo approval, simulated outreach, retirement/retry, then verify.
All source/borrower data is synthetic. The simulated clock is Monday 9 November
2026 at 10:00 WIB. `docs/evidence/demo.json` is local generated evidence, ignored by Git.
