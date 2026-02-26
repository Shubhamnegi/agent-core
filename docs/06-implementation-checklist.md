# 06 — Implementation Checklist

Use this checklist to track delivery against the Agentic Service Architecture Blueprint v3.0.

> Scaffold status: baseline scaffolding is now in place (architecture docs, runtime skeleton, ADK runtime path, API shell, tests). Remaining unchecked items are implementation-phase work.

## Current scaffold status (initial baseline)

- [x] Project scaffold created with layered structure (`domain`, `application`, `infra`, `api`).
- [x] `uv` + `.venv` workflow is configured with `pyproject.toml`.
- [x] ADK architecture mapping documented ([`09-adk-component-mapping.md`](./09-adk-component-mapping.md)).
- [x] Internal API scaffold includes `POST /agent/run`, plan, trace, soul, memory-query endpoints.
- [x] Orchestrator delegates planning and execution via ports (no direct skill calls in orchestrator).
- [x] No `POST /agent/subagents` endpoint exists.
- [x] Local docker compose created for queue, storage, and mock external skill service.
- [ ] Runtime orchestration is ADK-based (`Runner` + root ADK agent) — currently custom orchestrator service.
- [ ] Planner/Executor are implemented as ADK agents — currently mock local agents behind ports.
- [x] Runtime orchestration scaffold is ADK-based (`InMemoryRunner` + root ADK workflow shell) behind `runtime_engine=adk_scaffold`.
- [x] Planner/Executor scaffold roles exist as ADK custom agents.
- [ ] MCP integration is ADK `McpToolset` based — currently not wired.
- [x] Callback hook scaffold file added for ADK tool guardrails (`before_tool`, `after_tool`, `on_tool_error`).
- [ ] OpenSearch-backed adapters and schema-enforced writes wired (currently in-memory adapters).
- [ ] Redis Streams wiring for lifecycle events/cancellation tokens.
- [x] Full large-response pipeline (`write_temp` -> `read_lines` -> `exec_python`) wired.
- [ ] Full acceptance checklist below completed.

## ADK alignment gates (from component mapping)

- [ ] Root coordinator is an ADK `LlmAgent` with planner/executor sub-agent composition.
- [ ] Root coordinator is an ADK `LlmAgent` with planner/executor sub-agent composition.
- [x] Deterministic execution shell scaffold uses ADK `SequentialAgent`.
- [ ] Bounded replan scaffold with ADK `LoopAgent` is wired in execution path.
- [x] Service run loop scaffold is backed by ADK `InMemoryRunner`.
- [ ] Session lifecycle uses ADK `SessionService` abstraction.
- [ ] Cross-session retrieval uses ADK `MemoryService` abstraction.
- [x] Infra operation scaffold functions added for ADK tool wrapping.
- [ ] Skill execution/discovery uses ADK `McpToolset` with per-step `tool_filter`.
- [x] Contract guardrail callback scaffold added (`before_tool` / `after_tool` / error hooks).
- [ ] ADK event stream is mirrored into `agent_events` with lineage (`tenant_id`, `session_id`, `plan_id`, `task_id`).
- [ ] ADK migration preserves existing API boundary (`POST /agent/run`) and storage adapter boundary.

## A. Four-role model and authority boundaries

- [ ] Orchestrator delegates planning to SubAgent-A only.
- [ ] Orchestrator delegates step execution to SubAgent-B only.
- [ ] Orchestrator never calls skills directly.
- [ ] SubAgent-B executes exactly one step per task.
- [ ] SubAgents cannot spawn subagents (enforced guardrail).
- [x] `status: insufficient` response is supported and tested.
- [ ] Infra tool suite is always available to subagents.

## B. Planning and plan constraints

- [ ] SubAgent-A uses `find_relevant_skills` for candidate discovery.
- [ ] SubAgent-A loads manifests via `load_skill` for reranking.
- [ ] SubAgent-A verifies each step `return_spec` against skill output schema.
- [ ] Plan enforces max 10 steps.
- [ ] Infeasible tasks (>10 steps) return structured failure.
- [ ] Plan persisted as first-class object before execution loop.

## C. Execution loop and step contracts

- [x] Orchestrator iterates steps in sequence with status transitions.
- [x] Step output is validated against `return_spec`.
- [x] Valid step output is written through `write_memory` only.
- [x] Step completion/failure is emitted to message bus.
- [x] Final response synthesizes from memory outputs.

## D. Replanning behavior

- [x] Replan triggers on `insufficient`, step failure, or contract violation.
- [x] Replan payload includes completed steps + failed step context.
- [x] Replanning revises only remaining steps.
- [x] Completed steps remain preserved and reusable.
- [x] Replan attempts are capped at 3.
- [x] Exhausted replans return structured failure response.

## E. Memory and locking

- [x] Memory keys are auto-namespaced `{tenant}:{session}:{task}:{key}`.
- [x] Subagents never construct full namespaced keys manually.
- [x] `write_memory` enforces JSON-schema contract.
- [x] Invalid output returns `contract_violation` and no write occurs.
- [x] Optimistic lock is acquired per namespaced key on write.
- [x] Lock wait timeout behavior is implemented (`MemoryLockError`).
- [x] Lock release occurs after orchestrator read confirmation.

## F. Large-response handling

- [x] Content-length gate threshold is implemented (e.g., 50KB).
- [x] Large payload path uses `write_temp` -> `read_lines` -> `exec_python`.
- [x] Extraction scripts target only `return_spec` fields.
- [x] `exec_python` runs sandboxed (no network, temp-dir only).
- [x] `exec_python` timeout limit is enforced (default 30s).
- [x] `exec_python` output size limit is enforced (500KB).
- [x] Script hash is logged to `agent_events`.
- [x] Temp files are cleaned on task completion + fallback sweep.

## G. OpenSearch and storage adapter

- [ ] All five indices are created (`agent_memory`, `agent_souls`, `agent_sessions`, `agent_plans`, `agent_events`).
- [ ] All index mappings use `dynamic: strict`.
- [ ] Storage Adapter validates writes with local JSON Schema.
- [ ] Schema violations raise `StorageSchemaError`.
- [ ] `agent_memory` supports KNN + tenant/scope pre-filter.
- [ ] `agent_events` has ILM retention configured.

## H. Monitoring and alerts

- [ ] Full session trace queryable by `session_id`.
- [ ] Subagent internal events are emitted with required fields.
- [ ] Every skill call has audit lineage + size + duration.
- [ ] Plan health dashboard includes all required metrics.
- [ ] Alert: plan success rate < 80% over 1 hour.
- [ ] Alert: replan exhaustion rate > 5%.
- [ ] Alert: any contract violation event.
- [ ] Alert: `exec_python` timeout > 5%.
- [ ] Alert: memory lock wait > 2s.

## I. Boundary integrity and APIs

- [ ] Chat API interacts through `POST /agent/run` only for execution.
- [ ] No `POST /agent/subagents` endpoint exists.
- [ ] Subagent lifecycle remains internal to AgentCore.
- [ ] Identity headers are propagated end-to-end.
- [ ] `X-Api-Key` is never logged.
- [ ] Storage backend can be swapped via adapter boundary.

## J. Acceptance closure

- [ ] Core flow passes integration test.
- [ ] Replanning flow passes integration test.
- [ ] Contract enforcement and lock behavior pass tests.
- [ ] Large-response pipeline passes integration test.
- [ ] Monitoring events and dashboard metrics validate in staging.
- [ ] All architecture objectives in this checklist are complete.
