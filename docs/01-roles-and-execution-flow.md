# 01 — Roles and Execution Flow

## Objective

Implement the Manager–Planner–Executor–Infrastructure model with strict authority boundaries and single-step execution discipline.

## Four-role model

### Manager — Orchestrator (AgentCore)
- Receives user task from Chat API.
- Creates and persists plan object.
- Spawns Planner and Executor subagents.
- Owns execution loop, replanning, and final synthesis.
- Never calls skills directly.

### Planner — SubAgent-A
- Discovers skills via MCP.
- Loads manifests and output schemas.
- Reranks skills and builds progressive TODO plan.
- Adds step-level `return_spec` contracts.
- Verifies each `return_spec` is satisfiable.
- Returns plan with max 10 steps.

### Executor — SubAgent-B (n instances)
- Executes exactly one plan step.
- Uses only `allowed_skills` from Orchestrator.
- Handles large responses internally.
- Produces output matching `return_spec`.
- Writes validated output using infra tool `write_memory`.
- Emits finish signal on completion.

### Infrastructure — Infra Tool Suite
Always available to subagents:
- `write_memory`
- `read_memory`
- `write_temp`
- `read_lines`
- `exec_python`

## Hard rules

- SubAgents cannot spawn SubAgents.
- SubAgent-B returns `{ status: insufficient }` for multi-step needs.
- Skill access is auto-scoped by `cloud_skill_service`.
- No explicit access control logic in agent code.

## End-to-end flow

1. Chat API calls `POST /agent/run`.
2. Orchestrator loads soul/persona.
3. Orchestrator creates plan (status `pending`).
4. Orchestrator spawns SubAgent-A for plan generation.
5. Planner returns verified plan.
6. Orchestrator executes steps by spawning SubAgent-B per step.
7. SubAgent-B writes validated outputs to memory.
8. Orchestrator proceeds step by step to completion.
9. Orchestrator synthesizes final response and returns to Chat API.

## Deliverables

- Orchestrator orchestration loop.
- SubAgent-A planning contract.
- SubAgent-B execution contract.
- Message bus events for lifecycle transitions.
- Enforcement of no subagent nesting.
