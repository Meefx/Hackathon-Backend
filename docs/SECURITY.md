# Trust module threat boundaries

This is a synthetic local demo, not production security certification or legal approval.

| Boundary | Enforced control | Limit |
|---|---|---|
| Human / agent identity | Signed JWT, strict algorithm/issuer/audience/time validation, static roles | Demo identity selection is deliberately not production login |
| Entity / product | Server-bound token scope, scoped queries and composite dependency FKs | CF/LDN real contracts and mandates remain unverified |
| Knowledge ingestion | Size limits, structural chunking, PII/secret/injection rejection, no outbound URL fetch | Pattern checks cannot detect all personal data or attacks; human review is required |
| Retrieval | Approved/in-force/access filters before ranking, chunk IDs audited, no result cache | Local vectors are a nonsemantic baseline |
| Evidence lifecycle | Immutable artifacts, separate QA/human roles, hash-bound approval, one-way retirement | Human review correctness cannot be proven by software |
| Tools | Static agent/purpose allowlists; no model channel/SQL/file/network tools | Five-agent framework is not implemented here |
| Dispatch | HMAC exact-text/evidence binding, current source/state checks, serialized budget, unique attempt | Simulated DB delivery only; no exactly-once claim for external services |
| Audit | Same transaction, serialized hash chain, update/delete/truncate triggers, IDs/hashes only | Database owner can disable triggers; external anchoring is deferred |
| Conversations | Synthetic-only operational storage, restricted raw read, redacted model-context copy | Live recording, rights/retention workflows, transfer review and encryption design remain pilot gates |
| Availability | Bounded pool, connection/query/lock timeouts, no synchronous ingestion model dependency | No production availability or load-test claim |

Unknown timezone, expired evidence, stale facts, third-party contact, opt-out, paused/escalated/disputed state and classifier failures deny dispatch. Reuse of an idempotency key does not create new delivery permission. Search always reevaluates current knowledge status, even for a repeated request key.

No real borrower text, credentials, approval tokens, private reasoning or messages belong in logs, audit, reports or Git. Test fixtures and demonstrations use synthetic content. The provided research vault is not ingested as legally approved source material.
