# 04 — Monitoring and Observability

## Objective

Provide full operational visibility across orchestration, subagent execution, skill invocations, and plan quality.

## Four observability layers

### Layer 1 — Orchestrator trace
Session timeline by `session_id` from `agent_events`:
- Task receipt
- Planner spawn/return
- Plan persistence
- Step start/complete/failure
- Replan trigger/revision

### Layer 2 — SubAgent internals
Mandatory events:
- `skill.called`
- `large_response.detected`
- `python_script.executed`
- `memory.written`
- `contract_violation`
- `subagent.insufficient`
- `subagent.finish`

### Layer 3 — Skill call audit
Per invocation lineage and performance:
- Tenant/session/plan/task lineage
- Request/response size
- Duration
- Large-response trigger flag

### Layer 4 — Plan health dashboard
Metrics:
- Plan success rate
- Average replan count
- Step failure rate by skill
- Large-response frequency
- SubAgent p95 duration
- Contract violation rate
- Replan exhaustion rate

## Alerts

- Plan success rate < 80% over 1h
- Replan exhaustion > 5%
- Any contract violation event
- `exec_python` timeout > 5% of calls
- Memory lock wait > 2s

## Deliverables

- Standard event schema and emitters.
- Monitoring service for dashboard queries.
- Alert rules + runbook links.
- Trace endpoint parity with execution events.
