# 11 — ADK-Only Runtime Migration Checklist

Objective: remove the parallel custom orchestrator runtime path and run all request orchestration through ADK only.

## Status legend

- `[x]` Completed in this migration wave
- `[~]` In progress / partially completed
- `[ ]` Pending

## Phase A — Single runtime entrypoint

- [x] Enforce single execution path in API: `/agent/run` always routes to `AdkRuntimeScaffold`.
- [x] Remove runtime branching logic in API handler (`runtime_engine` switch removed from request path).
- [x] Remove direct container wiring for custom `AgentOrchestrator` in API container setup.
- [x] Remove mock planner/executor wiring from API container.
- [x] Keep response contract unchanged (`status`, `response`, `plan_id`).

## Phase B — Compatibility and config cleanup

- [x] Remove `runtime_engine` setting after one stable release window.
- [x] Remove any docs that describe runtime switching behavior.

## Phase C — Test boundary realignment

- [x] Update API boundary tests to ADK-only run path.
- [x] Remove test assumptions that mutate `container.runtime_engine`.
- [ ] Add explicit regression test: `/agent/run` ignores any runtime switch env and still uses ADK path.
- [ ] Add integration assertion: planner event must appear before executor event for normal runs.

## Phase D — Planner/executor control hardening inside ADK

- [ ] Replace prompt-only planner requirement with deterministic planner-first gate.
- [ ] Persist planner decision artifacts in `agent_events`.
- [ ] Enforce executor-only-after-planner invariant in runtime checks.
- [ ] Add tests for planner skip prevention.

## Phase E — Feature parity closure (custom flow -> ADK)

- [ ] Map and implement step-state transitions in ADK event lifecycle.
- [ ] Map and implement retry/replan decisions with bounded attempts.
- [ ] Map and implement contract-violation recovery paths.
- [ ] Map and implement memory write/read confirmation lifecycle.
- [ ] Add parity matrix doc showing old behavior vs ADK replacement.

## Phase F — Decommission legacy custom orchestrator code

- [x] Remove `AgentOrchestrator` service and unused support modules after parity completion.
- [x] Remove mock planner/executor implementations once no references remain.
- [x] Delete obsolete orchestrator unit tests after replacement ADK tests are in place.
- [x] Clean up dead ports/interfaces used only by removed path.

## Phase G — Operational readiness

- [ ] Validate OpenSearch trace lineage fields remain complete after removal.
- [ ] Run smoke tests for happy path, insufficient-data path, tool failure path, and replan exhaustion.
- [ ] Document rollback procedure (pin to previous release tag if needed).

## Phase H — Memory intelligence agent (new)

- [ ] Add a dedicated ADK memory agent responsible for memory fetch/save decisions.
- [ ] Grant memory agent tools for durable writes (`action_memory`, `user_memory`) and semantic retrieval.
- [ ] Ensure memory agent can summarize and pass only relevant memory context to orchestrator when required.
- [ ] Keep heavy memory retrieval/planning-context enrichment focused on planner path.
- [ ] Add routing rules: orchestrator invokes memory agent only when memory ops are needed.
- [ ] Add event lineage for memory agent actions (`memory.fetch`, `memory.save`, `memory.shared_to_orchestrator`).
- [ ] Add tests: cross-session retrieval, save/read correctness, and planner memory injection behavior.

## Notes

- This migration is intentionally staged to avoid contract regressions at API boundaries.
- The current wave executes Phase A, Phase F, and the config cleanup item in Phase B.