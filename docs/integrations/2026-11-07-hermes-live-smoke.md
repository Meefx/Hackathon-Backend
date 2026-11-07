# Single live Hermes collector brief evidence

Exactly one persisted task was admitted through the existing local REST service.
The task used the default Hermes API profile and synthetic account CF-1001.
The existing REST, worker, and MCP processes were left running while the other
chat merged its changes. PostgreSQL revision was confirmed as
0002_collection_state. This evidence covers those running services before the
merged application and migrations are restarted and applied.

## Persisted outcome

- Application run a069396e-0269-4b75-8352-7859aece5598.
- External Hermes run run_7b5dd7d3f09d4273bbea397ba90bbb7a.
- Manager session 09553e30-0013-4b79-8891-5cbb70a2f589.
- Admission returned HTTP202 and was submitted once with one UUID key.
- Backend completed with actual_runtime_mode hermes, outcome brief_created,
  and no reason codes. Elapsed time from admission to finish was 23.08355 seconds.
- Exactly one collector_brief artifact was persisted with ID
  93a8e0ee-e2ee-48f9-af6b-59a9de8d8dfb. The brief contains 657 characters.
- Artifact citation demo-sop-v1-contact matches approved ready run evidence.
  Both preflight and MCP retrieval snapshots explicitly use demo_fixture.
- FinalRuntimeResult passed schema validation. Application run, artifact IDs and
  citation IDs matched persisted data. candidate_message_id was null and
  review_requested was false.
- The run created zero messages. The synthetic account has zero dispatch receipts.

## Actual native tool evidence

Hermes retained tool.started and tool.completed events for these calls. Every
completed event had error=false.

1. tool_describe.
2. mcp__collectflow__get_account.
3. mcp__collectflow__get_contact_policy.
4. mcp__collectflow__retrieve_approved_context.
5. mcp__collectflow__submit_run_artifact.

The native discovery helper tool_describe also ran before the four requested
CollectFlow calls. The event stream contains no shell, file, channel, or dispatch
call. The actual MCP function naming uses double underscores in this runtime.
The runtime toolset name is mcp-collectflow, with collectflow as its alias.

## Actual runtime metadata

Hermes terminal status and run.completed metadata returned provider="" and
model="", with route_source="global". The served provider and model cannot be
identified from those empty fields. Usage was 58,234 input tokens, 1,005 output tokens,
59,239 total tokens. The actual_runtime_mode hermes is proven by the admitted
external run plus successful native MCP calls and persisted artifact.

This run exercised the default profile. Named specialist profile routes and
specialist delegation remain outside this run's verified evidence. The knowledge
source was the approved demo fixture. The other chat's merge and migrations need
separate post-merge verification by their owner.

## Discovery correction

Authenticated GET/v1/toolsets returns the object/platform/data envelope with 27
built-in configurable groups. Official _handle_toolsets calls
_get_platform_tools(config,"api_server",include_default_mcp_servers=False).
The endpoint therefore omits default MCP servers. The Runs agent factory calls
_get_platform_tools(config,"api_server") using the default True, and
_merge_mcp_servers adds configured enabled MCP servers unless restricted by an
explicit known-server selection, no_mcp, or disabled toolsets.

The live tool events above establish the actual default-profile MCP link.
No additional run or restart was performed.

Official source references

- https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/api_server.py#L2397
- https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/api_server.py#L2215
- https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/tools_config.py#L733
- https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference#tool-naming
