# 03 — Storage and OpenSearch Schemas

## Objective

Use OpenSearch as the single persistence backend with strict schema validation through a Storage Adapter.

## Index inventory

1. `agent_memory`
2. `agent_souls`
3. `agent_sessions`
4. `agent_plans`
5. `agent_events`

## Storage principles

- All index mappings use `dynamic: strict`.
- All writes pass through Storage Adapter.
- Storage Adapter validates with local JSON Schema before OpenSearch write.
- Schema violations throw `StorageSchemaError` and are never silently ignored.

## `agent_memory` (highlights)

- KNN enabled for semantic retrieval.
- Key fields: `tenant_id`, `session_id`, `task_id`, `key`, `scope`, `plan_id`, `step_index`.
- Concurrency metadata: `locked_by`, `lock_expires`.
- Retention metadata: `created_at`, `ttl_at`.

## `agent_plans` (highlights)

- First-class plan document.
- Nested `steps` with per-step state and timestamps.
- Nested `replan_history` with attempt and trigger context.
- Tracks `replan_count`, `status`, and completion markers.

## `agent_events` (highlights)

- Append-only event stream for traceability.
- Captures `event_type`, lineage IDs, `skill_id`, `script_hash`, payload, and timestamp.

## Retrieval requirement

`agent_memory` semantic queries must pre-filter by:
- `tenant_id`
- `scope`

## Operational requirement

`agent_events` must have ILM retention policy configured.

## Deliverables

- Storage Adapter interface and implementation.
- JSON Schema files per index.
- Migration/bootstrap scripts for all 5 indices.
- Pre-write schema validation tests.
