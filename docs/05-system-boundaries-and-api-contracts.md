# 05 — System Boundaries and API Contracts

## Objective

Maintain strict service boundaries so Chat, Agentic Service, Storage, and Skills remain independently evolvable.

## Boundary map

### Zone A — Chat API
- Owns user session/auth/streaming.
- Calls Agentic Service only.
- Forwards identity headers.
- Does not access OpenSearch or Skill Service directly.

### Zone B — Agentic Service
- Owns AgentCore, PromptBuilder, SubagentManager, ToolCoordinator.
- Stateless compute.
- Persists only through Storage Adapter.

### Zone C — Storage Adapter
- Translates domain store calls to OpenSearch operations.
- Enforces schema prior to write.

### Zone D — OpenSearch
- Hosts 5 strict indices.
- KNN in `agent_memory`.
- ILM for `agent_events`.
- Tenant guardrails with field-level controls.

### Zone E — Skill Service
- External dependency via MCP/HTTP.
- Access automatically scoped via `cloud_skill_service`.

### Zone F — Message Bus
- Redis Streams for lifecycle and orchestration signals.

## Internal API contracts

- `POST /agent/run`
- `GET /agent/plans/{plan_id}`
- `GET /agent/plans/{plan_id}/trace`
- `PUT /agent/souls/{tenant_id}`
- `GET /agent/memory/query`

Constraint:
- No public endpoint for subagent spawning.
- Subagent lifecycle is fully internal to AgentCore.

## Identity propagation

Required headers:
- `X-Tenant-Id`
- `X-User-Id`
- `X-Session-Id`
- `X-Api-Key` (forwarded to skills, never logged)

## Deliverables

- API contract definitions (OpenAPI/proto).
- Header propagation middleware.
- Tenant-safe logging rules.
- Boundary contract tests between zones.
