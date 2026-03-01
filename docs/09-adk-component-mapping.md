# 09 — Google ADK One-to-One Component Mapping

## Objective

Map the Agentic Service v3.0 architecture (Manager–Planner–Executor–Infrastructure) directly to Google ADK components we should use.

## Source basis

This mapping is based on:
- https://google.github.io/adk-docs/
- https://google.github.io/adk-docs/agents/
- https://google.github.io/adk-docs/agents/multi-agents/
- https://google.github.io/adk-docs/agents/workflow-agents/
- https://google.github.io/adk-docs/sessions/
- https://google.github.io/adk-docs/events/
- https://google.github.io/adk-docs/callbacks/
- https://google.github.io/adk-docs/mcp/
- https://google.github.io/adk-docs/tools-custom/mcp-tools/
- https://google.github.io/adk-docs/runtime/
- https://google.github.io/adk-docs/observability/
- https://google.github.io/adk-docs/api-reference/python/

## Architecture → ADK mapping (one-to-one)

| Architecture component (v3) | ADK component | How we use it in this project |
| :--- | :--- | :--- |
| Manager (Orchestrator / AgentCore) | `LlmAgent` as coordinator, optionally wrapped by `Runner` | Root coordinator agent for request intake, delegation policy, and final synthesis. |
| Planner (SubAgent-A) | Dedicated `LlmAgent` with planning prompt + planner primitives (`BuiltInPlanner` / `PlanReActPlanner`) | Produces stepwise plan with `return_spec` contract per step and feasibility checks (≤10 steps). |
| Executor (SubAgent-B, one step at a time) | Dedicated `LlmAgent` or `BaseAgent` per execution role | Executes exactly one plan step and returns structured result/status (`ok`, `failed`, `insufficient`). |
| Infra tool suite (`write_memory`, `read_memory`, `write_temp`, `read_lines`, `exec_python`) | ADK `FunctionTool` / `BaseTool` set | Implement each infra function as ADK tools and bind to planner/executor agents. |
| Skill discovery + skill execution via MCP | `McpToolset` (`google.adk.tools.mcp_tool`) | Planner uses discovery/load skills; executor gets MCP endpoint tools plus infra tools. |
| Executor MCP exposure | `McpToolset` per resolved endpoint | Build executor MCP toolsets from resolved endpoint configs at request time. |
| SubAgents cannot spawn SubAgents | Agent hierarchy policy + callback/plugin guard | Enforce in `before_tool`/policy logic: block transfer/spawn attempts from executor context. |
| Plan object as first-class persisted document | Custom `PlanRepository` + OpenSearch adapter | ADK does not provide an OpenSearch plan schema; keep this as our storage adapter contract. |
| Plan states (`pending`, `planning`, `executing`, `replanning`, `complete`, `failed`) | ADK Events + custom plan persistence | Use ADK event stream for runtime transitions and persist canonical plan status in `agent_plans`. |
| Replanning (max 3, preserve completed steps) | `LoopAgent` for bounded retry control + custom merge logic | Loop controls retry count; custom orchestrator logic merges revised remaining steps only. |
| Session context per conversation | `SessionService` + `Session` + `State` | ADK session state backs runtime context; map tenant/user/session IDs in app-level keys/metadata. |
| Cross-session memory | `MemoryService` abstraction | Keep ADK memory interface for retrieval; persist canonical memory in OpenSearch via adapter. |
| Event sourcing / execution trace | `Event` (`google.adk.events.Event`) stream | Mirror ADK events into `agent_events` with lineage fields (`tenant/session/plan/task`). |
| Skill/tool audits | Tool callbacks + events + telemetry | Capture tool input/output size, duration, and status via callbacks/plugins + event writes. |
| Contract enforcement at write boundary | `before_tool`/`after_tool` callbacks or plugin | Validate `return_spec` shape before commit; reject on mismatch and emit violation event. |
| Large payload handling gate | Tool logic + optional code executor (`BuiltInCodeExecutor`/custom) | Implement threshold route to temp file + selective extraction before memory write. |
| Runtime run loop | `Runner` / `InMemoryRunner` (dev), runtime API modes | Use ADK runner for local orchestration; expose service APIs from our FastAPI boundary. |
| Resume capability | ADK runtime resume + persistent session backend | Align with ADK resume primitives while preserving our plan/memory stores. |
| Observability layers | ADK logging/observability + plugin callbacks + telemetry helpers | Map orchestrator trace/subagent internals/skill audit/plan health via structured ADK callbacks and events. |
| Chat API boundary (`POST /agent/run`) | Our FastAPI wrapper over ADK runner | Keep existing boundary; ADK remains internal engine, not external contract replacement. |
| Storage adapter boundary | Custom adapter (OpenSearch) | Keep hard boundary exactly as v3 doc; ADK services integrate through this adapter layer. |

## Recommended ADK assembly for our codebase

1. Root coordinator: `LlmAgent` with planner/executor roles as sub-agents.
2. Deterministic orchestration shell: `SequentialAgent` for strict step progression.
3. Retry shell: `LoopAgent` around failed-step replan path with max=3.
4. Skill integration: `McpToolset` for planner discovery and executor endpoint tools.
5. Execution telemetry and policy enforcement: callbacks/plugins (`before_tool`, `after_tool`, `on_tool_error`, `on_event`).
6. Runtime: `Runner` + persistent `SessionService` and `MemoryService` implementations bridged to storage adapter.

## Non-negotiable custom parts (not replaced by ADK)

These remain project-owned because they are architecture-specific:
- OpenSearch index contracts (`agent_memory`, `agent_plans`, `agent_events`, `agent_sessions`, `agent_souls`).
- Strict `return_spec` schema contract and write gate semantics.
- Memory key namespacing format and lock lifecycle.
- Replan merge semantics (preserve completed steps, revise remaining only).
- Internal API contract and zone boundaries from the architecture blueprint.

## Implementation order aligned to checklist

1. Introduce ADK runner + root coordinator while preserving current API routes.
2. Convert planner/executor interfaces to ADK agents.
3. Implement infra tools as ADK `FunctionTool`s.
4. Wire MCP skills through `McpToolset` with allow-list filtering.
5. Add callback/plugin-based contract checks + event enrichment.
6. Swap in OpenSearch-backed session/memory/plan/event adapters.
7. Add replan loop control and exhaustion handling with deterministic tests.

## Decision summary

- Yes, this architecture should explicitly use Google ADK as the agent runtime framework.
- ADK covers agent composition, orchestration primitives, session/memory abstractions, tools, MCP integration, callbacks, events, and runtime lifecycle.
- Our service keeps ownership of storage schemas, contract enforcement details, and boundary-specific API behavior.
