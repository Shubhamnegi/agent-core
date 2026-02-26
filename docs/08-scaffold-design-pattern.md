# 08 — Scaffold Design Pattern Notes

## Objective

Keep the code easy to alter (pluggable adapters) while preserving simple debug paths (low indirection in runtime flow).

## Pattern used

- **Hexagonal-lite (Ports and Adapters)** at service boundaries.
- **Use-case oriented orchestration** in one explicit `AgentOrchestrator` flow.
- **Simple dependency container** in API startup for transparent wiring.

## Why this balance works

- Easy alteration:
  - Swap adapters (in-memory -> OpenSearch/Redis/MCP) without rewriting use-cases.
  - Keep external integrations behind narrow protocol interfaces.
- Easy debugging:
  - Core flow remains in one place: `application/services/orchestrator.py`.
  - Request tracing is explicit via `X-Request-Id` and structured logs.
  - Mock planner/executor let you reproduce cases quickly.

## Guardrails for maintainability

- Avoid deep inheritance trees.
- Keep orchestration logic in application layer only.
- Keep external calls in infra adapters only.
- Keep domain models free from framework dependencies.

## Next adapter upgrades

1. OpenSearch repositories for plans/memory/events/souls.
2. Redis Streams for lifecycle and subagent events.
3. Skill service gateway with allow-list enforcement.
4. Infra tools for large-response extraction pipeline.
