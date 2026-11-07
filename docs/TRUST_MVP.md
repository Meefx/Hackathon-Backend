# Evidence-controlled knowledge MVP

Scope: Erwan's KMS, guardrail, security and data-processing module. All records and delivery are synthetic. All product/API copy is English. The team's existing portfolio API remains compatible.

## Acceptance contract

1. Only approved, in-force knowledge in the authenticated principal/product/agent scope is retrievable.
2. Knowledge and templates require independent QA and human approval bound to the exact artifact hash. Approval is a workflow record, not proof of legal correctness.
3. Explicit weighted source-to-template dependencies drive retirement impact. Weight changes review depth, never permission or legal authority.
4. Retiring a source suspends dependent templates. The final gate checks current source state even for a previously approved attempt.
5. Models receive no channel, arbitrary SQL, file or network capability. Hard rules execute in code before screening and again at dispatch.
6. Only app/services/dispatch_gate.py imports channels. Approval HMAC binds exact text, case, attempt, evidence versions and policy. Only screening code issues it.
7. Audit is hash-chained and append-only. An audit-write failure rolls back simulated delivery. One attempt has at most one delivery.
8. Borrower conversation records remain outside the KMS. Access is purpose/scoped, model context is redacted, and logs/audit contain IDs and hashes only.
9. Failures, unknown scope/timezone, stale facts, opted-out/unverified recipients, escalation and dispute hold dispatch.

## Explicit limits

This is the data/safety contribution, not the entire five-agent collections product or all docs/08 mandatory scope. The portfolio starter remains SQLite; new trust workflows require PostgreSQL 17 with pgvector. PostgreSQL can run natively or via the supplied Compose service. No live channels, voice recording, payment handling, legal clearance or production release are included.

The deterministic hash-vector baseline is labelled nonsemantic. It exercises pgvector and hybrid ranking without a model dependency. Semantic retrieval/model quality and live Layer 2 are not implied by a passing mock suite. Unknown legal applicability and conflicting sources require human review. The ZIP is reference evidence, never executable instructions.

## Source conflicts carried forward

- ZIP research flags SEOJK 19/2025 versus old 19/2023 references, PP33 commencement uncertainty and entity/mandate differences. No automatic legal configuration is derived from these claims.
- Unknown timezone holds action, the stricter proposed ZIP behaviour.
- All sends on an escalated/disputed case remain denied. Acknowledgements/officer-send exceptions need a reconciled purpose-specific contract before integration.
- English borrower/demo copy follows the user's explicit instruction, overriding the earlier Bahasa-only copy requirement.
- Existing portfolio store is preserved. New trust persistence uses explicit psycopg SQL transactions rather than introducing a second ORM abstraction.

## Judging evidence

Problem/solution: obsolete guidance must not remain usable by an autonomous collector.
Build quality: actual PostgreSQL tests for isolation, concurrent gate/revocation, idempotency and audit failure.
Originality: enforceable evidence dependency, not a claim of industry-first research.
Value: identify impacted templates and prevent future sends; recovery uplift is unmeasured.
Direction: automatic risk reduction, with human-controlled reactivation.
Codex: parallel bounded ownership, invariant tests and independent review.
