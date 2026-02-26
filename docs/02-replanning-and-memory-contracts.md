# 02 — Replanning and Memory Contracts

## Objective

Enable surgical replanning with strong step contracts and safe memory writes.

## Replanning design

### Triggers
- SubAgent-B returns `status: insufficient`.
- SubAgent-B step failure (timeout, skill error, schema mismatch).
- `write_memory` contract validation failure.

### Rules
- Maximum 3 replan attempts.
- Preserve completed steps.
- Revise only remaining steps.
- Stop with structured failure when attempts are exhausted.

### Replan input payload
Planner receives:
- `original_task`
- `completed_steps`
- `failed_step` with reason/suggestion
- `remaining_steps`

### Structured terminal failure
```json
{
  "status": "failed",
  "reason": "max replan attempts reached",
  "completed_steps": [],
  "last_failure": { "step": 0, "reason": "" }
}
```

## Plan lifecycle states
- `pending`
- `planning`
- `executing`
- `replanning`
- `complete`
- `failed`

## Memory key strategy

### Namespacing format
`{tenant_id}:{session_id}:{task_id}:{key}`

### Locking model
- Optimistic lock per full namespaced key.
- Lock wait timeout: 5s.
- Conflicting write returns `MemoryLockError`.
- Lock released after orchestrator read confirmation.

## Contract enforcement (`write_memory`)

Validation sequence:
1. Validate against `return_spec.shape`.
2. Reject and return `contract_violation` if invalid.
3. If valid: acquire lock, write, emit event, release lock.

## Deliverables

- Replan controller with bounded retries.
- Plan merge logic that preserves completed work.
- Centralized memory validator + lock handling.
- Memory contract violation events and alerts.
