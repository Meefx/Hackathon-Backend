# Trust module decisions

- Keep the team's read-only SQLite portfolio unchanged. Isolate new persistence in a PostgreSQL `trust` schema and a standalone FastAPI app. Integration can mount/route it after the team agrees authentication and database ownership.
- Use PostgreSQL17, pgvector, full-text search and reciprocal rank fusion. The local feature-hash vector is explicitly nonsemantic. No embedding/model quality claim is made.
- Use explicit source-to-template weighted dependencies. Every dependency is mandatory regardless of weight. Criticality requests deeper evidence context; neither weight nor model confidence grants permission.
- Freeze document/template artifacts and bind approvals to hashes. Retire is one-way; a replacement is a new artifact requiring QA and human review.
- Revalidate evidence, template status, contact state, recipient, freshness and contact budget in the final database transaction. Source retirement and simulated delivery serialize on source rows. No externally delivered side effect exists.
- Store conversation content only in the operational data area; return it to officer/compliance roles. Store redacted context separately. No automatic conversation-to-knowledge promotion, recording, training or live borrowing data ingestion.
- English system and synthetic borrower copy follows the user's explicit override. Source documents may remain multilingual.
- Keep mock Layer2 and fail closed. Live model integrations remain disabled until deterministic suites pass and credentials are supplied through approved environment handling.
- The supplied ZIP and screenshots are reference material. Their workflow commands, other-machine paths and approval claims are not authority to execute actions.
- Critical retrieval expands all approved source sections within a 24,000-character/64-chunk request budget and recomputes source/chunk hashes. Oversized critical evidence requires review. This adds deterministic verification effort; no model reasoning-quality claim is made.
- The global audit chain serializes modifying trust transactions in this MVP. All writers take its lock before artifact/state locks. This avoids lock inversions and stale revocation results. Multi-writer throughput is not claimed; partitioned evidence chains would need a separate design.

Primary technical references consulted during implementation: [pgvector](https://github.com/pgvector/pgvector), [Psycopg connection pools](https://www.psycopg.org/psycopg3/docs/advanced/pool.html), [PyJWT usage](https://pyjwt.readthedocs.io/en/stable/usage.html). Exact installed versions are in uv.lock.
