# Codex build log

7 November 2026, WIB. Parallel work used subagents inside the current chat; no
additional user-owned chats were created.

| Phase | Work | Observed result |
|---|---|---|
| Repository setup | Clone requested team repo, pull main, create Erwan branch | Main up to date; baseline 9b365ee |
| Read-only research | Read 32 Markdown and 5 Obsidian configuration files in supplied ZIP; split rules/product, source evidence and scalability | Research treated as reference; no document workflow executed |
| Foundation | Native PostgreSQL17+pgvector, Python dependencies, injected clock, SQL schema, audit | Schema initialized; eight portfolio tests remained green |
| Parallel implementation | Knowledge lifecycle/graph; security and processing; safety tests | Bounded file ownership; parent integrated API, runtime, gate, demo and docs |
| Verification | Real PostgreSQL integration, architectural import checks, concurrent delivery and revocation, failure injection | 277 passed; lint and mypy green |
| Independent review | Reviewed time after locks, label parsing, lock ordering, token binding and idempotent access | Reproduced bugs fixed with regression tests |
| Demonstration | Scripted API loop and local benchmark | Retrieval excludes retired source; template suspended; subsequent attempt NOT_SENT; audit valid |
| Visual QA | Attempted browser access to local console | Browser permission declined; visual QA not claimed |

Exact command summaries and limitations are in 09_Implementation_Report.md.
