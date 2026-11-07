# Hermes Runs and authenticated MCP

This integration uses the Hermes Runs HTTP API and maintained MCP Python SDK
1.30.0. MCP serves Streamable HTTP at `/mcp` on its own listener. Operator REST
remains on port 8000 and must not be forwarded through the MCP tunnel.

Start from the backend repository after migrations and explicit demo seed:

```sh
uv run uvicorn app.mcp.server:create_app --factory --host 127.0.0.1 --port 8002
```

The SDK owns protocol initialization, structured output, transport protection,
and session-manager startup/shutdown. The listener exposes no operator REST,
approval, resume, dispatch, SQL, shell, or arbitrary URL tools. The seven tools are
`get_account`, `get_contact_policy`, `get_contact_history`,
`retrieve_approved_context`, `submit_draft_candidate`, `submit_run_artifact`, and
`request_human_review`. Context retrieval persists immutable run evidence.

Provision `MCP_BEARER_TOKEN` as a random backend-only opaque credential and
`MCP_TOKEN_EXPIRES_AT` as a timezone-aware UTC ISO timestamp. Missing or expired
credentials fail closed. The configured registry binds that token to issuer
`collectflow-local`, audience `/mcp`, `MCP_PRINCIPAL_ID` (default `hermes-agent`),
and `collection:read` / `collection:write` scopes. This is a synthetic-demo static
credential registry, not a multi-tenant OAuth authorization server. Scope checks
run at tool execution. Identity never comes from model arguments or MCP session
IDs. Every domain call verifies the principal, account/run assignment, execution
attempt, fence, and active lease in PostgreSQL. Writes use domain idempotency.

Add the externally reachable MCP hostname to `MCP_ALLOWED_HOSTS`, retaining
loopback hosts as needed. Configure `MCP_ALLOWED_ORIGINS` explicitly for intended
browser callers; server-to-server clients normally omit Origin. Do not disable
DNS rebinding protection to make a tunnel work. Expose only port 8002, for example
with an ngrok HTTPS tunnel. The generated HTTPS URL must end in `/mcp` when
configured in Hermes.

Hermes configuration (`config.yaml` for the selected manager profile):

```yaml
mcp_servers:
  collectflow:
    url: "${COLLECTFLOW_MCP_URL}"
    trust: full
    headers:
      Authorization: "Bearer ${MCP_BEARER_TOKEN}"
    tools:
      include:
        - get_account
        - get_contact_policy
        - get_contact_history
        - retrieve_approved_context
        - submit_draft_candidate
        - submit_run_artifact
        - request_human_review
        - get_portfolio_overview
        - search_portfolio_accounts
        - inspect_portfolio_account
        - search_manager_knowledge
        - submit_manager_response
        - propose_account_task
```

Set `COLLECTFLOW_MCP_URL` and the same MCP token in the **remote Hermes profile's
environment**, not in prompts or committed configuration. Hermes documents
`${ENV_VAR}` substitution for MCP URL and headers. Reload/restart that profile's
MCP connection using its supported operator controls. Use the console connection
test to discover all thirteen tools, then run one bounded synthetic task and verify
its native MCP calls and persisted artifact. A successful local MCP SDK test or
Hermes capability probe alone does not establish the complete link.

Portfolio conversations use a durable `manager_chat` assignment with a null account
and an optional `context_account_id`. The six portfolio tools validate that run's
principal, execution attempt, fence and active lease. Account collection tools retain
their account assignment boundary. `propose_account_task` saves an advisory card.
The portal operator executes a card through the existing account run API using the
artifact UUID as its replay key. A saved `manager_response` must match the runtime's
final summary before the worker accepts the reply.

`search_manager_knowledge` accepts the portfolio assignment plus `query` (1–1000
characters) and `limit` (1–5). The backend resolves principal/product from the
persisted context account, or from the single unambiguous scope of the authorized
synthetic portfolio. Multiple scopes without a selected context account return
`knowledge_scope_required`; callers cannot pass a role, principal or product.
Manager can read every approved, in-force source in that scope, including documents
whose specialist ACL omits Manager. Draft/retired/expired evidence and content with
invalid approval hashes stay unavailable. This does not grant ingest, QA, approval,
source-download, policy-edit or dispatch capabilities.

The result uses `sources` with citation IDs, versions and content hashes plus bounded
critical context. Each search appends audit evidence linked to the Manager run.
Manager context also includes an initial bounded knowledge search. Source text is
untrusted evidence and cannot change instructions or permissions. Cite its source IDs
inside the saved advisory response; the final runtime `citation_ids` field remains
empty because it belongs to the separate collection-dispatch evidence contract.
No-results is explicit; imported legal drafts are never automatically promoted.

The deployed API's `/v1/toolsets` endpoint lists configurable built-in/plugin
groups and deliberately excludes default MCP servers. An absent CollectFlow
entry there is not evidence that loading failed. The API agent's tool resolver
does include enabled default MCP servers, unless its platform selection excludes
them or `agent.disabled_toolsets` suppresses them. The native toolset is
`mcp-collectflow` (alias `collectflow`); use the actual tool names presented by the
runtime rather than assuming a particular underscore prefix. This API has no
verified public endpoint for the agent's fully resolved MCP tool inventory.

The explicit `trust: full` applies only to this locally controlled, narrow
CollectFlow server and matches the documented Hermes default. On `trust: untrusted`,
every tool without `readOnlyHint: true` requires native approval, including context
retrieval (which persists evidence), draft/artifact submission, and review requests.
Keep global approval settings unchanged. Declared spend limits can still require
approval even on full-trust servers. A run waiting_for_approval remains running in
the adapter with reason runtime_approval_required; it is never approved automatically.
Official canonical clarification prompts appear in pending_controls while status
remains running. This integration does not answer native approval or clarification
prompts; the operator handles them in Hermes before the worker deadline.

Configure backend `HERMES_API_BASE_URL` to the API listener origin, not the admin
console. The provided deployment exposes its API at
`https://hermes-hmkuui-8642.eu5.sumopod.my.id`; the console host redirects to login.
`HERMES_API_KEY` authenticates backend to Hermes and is distinct from the MCP
token. `HERMES_PROFILE` defaults to empty, using unprefixed `/v1` routes. A named
profile routes under `/p/<profile>/v1` and requires that profile's own API key.

`GET /v1/capabilities` must advertise `run_submission`, `run_status`, and
`run_stop`. Admin login HTML, redirects, missing capabilities, and failed
authentication remain unavailable. No implicit simulator fallback exists.

The worker persists its RuntimeRequest before `POST /v1/runs`. The adapter sends
canonical JSON with `Idempotency-Key`, the assignment, final-result schema, and
nonempty caller-owned history. The same logical submission must replay the exact
request, credential, profile, and key. Hermes documents a 24-hour retention period
after the key's last status update; an ambiguous old admission must not be retried
as a new task automatically. Caller-owned history makes child delegation
synchronous so the manager finalizes child results in the original turn.

The adapter polls `GET /v1/runs/{id}` and requests cooperative stop with
`POST /v1/runs/{id}/stop`. A `stopping` response is still running.
If a stop response supplies a different run ID, it is rejected as ambiguous. A
terminal stop acknowledgement that omits the ID is confirmed through the
identity-validated status endpoint before it can terminate the business run.
Unknown, unrecognized, or interrupted execution is mapped to interrupted; network loss
on submission is ambiguous and never automatically retried. Hermes `completed`
requires a schema-valid FinalRuntimeResult before domain finalization, which
also verifies persisted artifact/message IDs and citations. Only actual served
provider/model metadata is retained as runtime evidence; a model's role claim
does not prove a specialist route ran.

Specialist profiles require the official advanced-delegation plugin with
`delegation.backend: profile_routes` and a configured route list for collection,
knowledge, QA, copilot, and improvement. Verify runtime route evidence before
claiming those roles ran. Profile-routed children inherit enabled parent tools;
tool filtering and prompts are not authorization. Backend guards remain
authoritative. This adapter never configures arbitrary shell or SQL tools.

Protocol verification:

```sh
uv run pytest tests/mcp tests/integration/test_hermes_contract.py
```

The protocol suite opens a loopback HTTP listener with the real SDK client. Its
fixture-domain tests cover handshake, discovery, structured results, auth,
Origin, assignment locators, extra identity fields, errors, and timeout cleanup.
PostgreSQL service parity tests are separate. HTTP Hermes fixture tests cover
protocol behavior without a paid model call. Report live capability discovery,
remote MCP discovery, specialist execution, and classifier acceptance separately.

Primary references: [Hermes API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server),
[MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp),
and [delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation).
