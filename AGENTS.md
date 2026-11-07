# CollectFlow trust contribution

Read the parent Graceline AGENTS.md and docs/TRUST_MVP.md before safety changes.
The user owns KMS, guardrail, security and data processing. Preserve team portfolio
API contracts. Pull the latest main before new work; never discard local changes.

Current status: standalone synthetic trust MVP, local PostgreSQL17+pgvector;
277 tests passing, Ruff and mypy green. See docs/09_Implementation_Report.md.

- All new UI and synthetic borrower copy is English, per user instruction.
- No secrets or real borrower data. Never read/edit/print .env or .env.* except
  .env.example, or *.pem/*.key. No tokens or raw messages in logs/audit/commits.
- Only app/services/dispatch_gate.py imports channels. Models have no side-effect tools.
- Hard rules and current evidence are enforced in code and at dispatch. Fail closed.
- Preserve all safety tests. New domain time comes from app/core/clock.py.
- After Python edits: uv run ruff format <files>. Console: npx prettier --write <file>.
- Test requires an explicit disposable PostgreSQL DSN via TRUST_TEST_DATABASE_URL.
- Commands: uv sync --locked; uv run pytest -q; uv run ruff check .; uv run mypy app.
- Demo: TRUST_DATABASE_URL=<local-dsn> uv run python -m app.trust.demo --serve.
- Hash vectors and Layer2 are declared demo baselines. Do not claim live model quality.
- Commit verified phases. Do not push until the human asks.
