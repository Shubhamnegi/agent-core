from __future__ import annotations

from typing import Any

from agent_core.domain.exceptions import StorageSchemaError

INDEX_AGENT_MEMORY = "agent_memory"
INDEX_AGENT_SOULS = "agent_souls"
INDEX_AGENT_SESSIONS = "agent_sessions"
INDEX_AGENT_PLANS = "agent_plans"
INDEX_AGENT_EVENTS = "agent_events"

ALL_INDEXES = [
    INDEX_AGENT_MEMORY,
    INDEX_AGENT_SOULS,
    INDEX_AGENT_SESSIONS,
    INDEX_AGENT_PLANS,
    INDEX_AGENT_EVENTS,
]

EVENTS_ILM_POLICY = "agent-events-retention-policy"

# Why 768: practical default for text embedding vectors while keeping index payloads moderate.
DEFAULT_EMBEDDING_DIMS = 768


def resolve_index_name(base_name: str, prefix: str = "") -> str:
    if not prefix:
        return base_name
    return f"{prefix}_{base_name}"


def build_index_definition(
    index_name: str,
    embedding_dims: int = DEFAULT_EMBEDDING_DIMS,
) -> dict[str, Any]:
    if index_name == INDEX_AGENT_MEMORY:
        return {
            "settings": {
                "index": {
                    "knn": True,
                }
            },
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "namespaced_key": {"type": "keyword"},
                    "tenant_id": {"type": "keyword"},
                    "session_id": {"type": "keyword"},
                    "task_id": {"type": "keyword"},
                    "scope": {"type": "keyword"},
                    "key": {"type": "keyword"},
                    "value": {
                        "type": "object",
                        "enabled": True,
                        "dynamic": True,
                    },
                    "return_spec_shape": {
                        "type": "object",
                        "enabled": True,
                        "dynamic": True,
                    },
                    "created_at": {"type": "date"},
                    "updated_at": {"type": "date"},
                    "embedding": {"type": "knn_vector", "dimension": embedding_dims},
                },
            },
        }

    if index_name == INDEX_AGENT_EVENTS:
        return {
            "settings": {
                "index": {
                    "plugins.index_state_management.policy_id": EVENTS_ILM_POLICY
                }
            },
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "event_type": {"type": "keyword"},
                    "tenant_id": {"type": "keyword"},
                    "session_id": {"type": "keyword"},
                    "plan_id": {"type": "keyword"},
                    "task_id": {"type": "keyword"},
                    "payload": {
                        "type": "object",
                        "enabled": True,
                        "dynamic": True,
                    },
                    "ts": {"type": "date"},
                },
            },
        }

    if index_name == INDEX_AGENT_PLANS:
        return {
            "settings": {},
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "plan_id": {"type": "keyword"},
                    "tenant_id": {"type": "keyword"},
                    "session_id": {"type": "keyword"},
                    "user_id": {"type": "keyword"},
                    "status": {"type": "keyword"},
                    "replan_count": {"type": "integer"},
                    "steps": {"type": "object", "enabled": True},
                    "replan_history": {"type": "object", "enabled": True},
                    "created_at": {"type": "date"},
                    "completed_at": {"type": "date"},
                },
            },
        }

    if index_name == INDEX_AGENT_SOULS:
        return {
            "settings": {},
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "tenant_id": {"type": "keyword"},
                    "user_id": {"type": "keyword"},
                    "payload": {"type": "object", "enabled": True},
                    "updated_at": {"type": "date"},
                },
            },
        }

    if index_name == INDEX_AGENT_SESSIONS:
        return {
            "settings": {},
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "session_id": {"type": "keyword"},
                    "tenant_id": {"type": "keyword"},
                    "user_id": {"type": "keyword"},
                    "state": {"type": "object", "enabled": True},
                    "created_at": {"type": "date"},
                    "updated_at": {"type": "date"},
                },
            },
        }

    msg = "unsupported_index_definition"
    raise ValueError(msg)


def build_events_ilm_policy(retention_days: int = 30) -> dict[str, Any]:
    # Why this policy: events are high-volume operational telemetry and should expire automatically.
    return {
        "policy": {
            "policy_id": EVENTS_ILM_POLICY,
            "description": "Retention policy for agent events",
            "default_state": "hot",
            "states": [
                {
                    "name": "hot",
                    "actions": [],
                    "transitions": [
                        {
                            "state_name": "delete",
                            "conditions": {"min_index_age": f"{retention_days}d"},
                        }
                    ],
                },
                {
                    "name": "delete",
                    "actions": [{"delete": {}}],
                    "transitions": [],
                },
            ],
        }
    }


LOCAL_DOCUMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    INDEX_AGENT_MEMORY: {
        "required": {
            "namespaced_key": "string",
            "tenant_id": "string",
            "session_id": "string",
            "task_id": "string",
            "scope": "string",
            "key": "string",
            "value": "object",
            "return_spec_shape": "object",
            "created_at": "string",
            "updated_at": "string",
        },
        "optional": {
            "embedding": "array_number",
        },
    },
    INDEX_AGENT_EVENTS: {
        "required": {
            "event_type": "string",
            "tenant_id": "string",
            "session_id": "string",
            "payload": "object",
            "ts": "string",
        },
        "optional": {
            "plan_id": "string_or_null",
            "task_id": "string_or_null",
        },
    },
    INDEX_AGENT_PLANS: {
        "required": {
            "plan_id": "string",
            "tenant_id": "string",
            "session_id": "string",
            "user_id": "string",
            "status": "string",
            "replan_count": "integer",
            "steps": "array",
            "replan_history": "array",
            "created_at": "string",
        },
        "optional": {
            "completed_at": "string_or_null",
        },
    },
    INDEX_AGENT_SOULS: {
        "required": {
            "tenant_id": "string",
            "payload": "object",
            "updated_at": "string",
        },
        "optional": {
            "user_id": "string_or_null",
        },
    },
    INDEX_AGENT_SESSIONS: {
        "required": {
            "session_id": "string",
            "tenant_id": "string",
            "user_id": "string",
            "state": "object",
            "created_at": "string",
            "updated_at": "string",
        },
        "optional": {},
    },
}


def validate_document_schema(index_name: str, document: dict[str, Any]) -> None:
    schema = LOCAL_DOCUMENT_SCHEMAS.get(index_name)
    if schema is None:
        msg = f"schema_not_found:{index_name}"
        raise StorageSchemaError(msg)

    required_fields = schema.get("required", {})
    optional_fields = schema.get("optional", {})
    allowed_fields = set(required_fields) | set(optional_fields)

    for field in required_fields:
        if field not in document:
            msg = f"storage_schema_error: missing required field '{field}'"
            raise StorageSchemaError(msg)

    for field_name in document:
        if field_name not in allowed_fields:
            msg = f"storage_schema_error: unexpected field '{field_name}'"
            raise StorageSchemaError(msg)

    for field_name, expected_type in required_fields.items():
        _ensure_type(field_name, document[field_name], expected_type)

    for field_name, expected_type in optional_fields.items():
        if field_name in document:
            _ensure_type(field_name, document[field_name], expected_type)


def _ensure_type(field_name: str, value: Any, expected_type: str) -> None:
    if expected_type == "string" and not isinstance(value, str):
        raise StorageSchemaError(f"storage_schema_error: field '{field_name}' must be string")

    if expected_type == "integer" and not (
        isinstance(value, int) and not isinstance(value, bool)
    ):
        raise StorageSchemaError(f"storage_schema_error: field '{field_name}' must be integer")

    if expected_type == "object" and not isinstance(value, dict):
        raise StorageSchemaError(f"storage_schema_error: field '{field_name}' must be object")

    if expected_type == "array" and not isinstance(value, list):
        raise StorageSchemaError(f"storage_schema_error: field '{field_name}' must be array")

    if expected_type == "array_number":
        if not isinstance(value, list):
            raise StorageSchemaError(f"storage_schema_error: field '{field_name}' must be array")
        if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            msg = f"storage_schema_error: field '{field_name}' must contain only numbers"
            raise StorageSchemaError(msg)

    if expected_type == "string_or_null" and not (value is None or isinstance(value, str)):
        msg = f"storage_schema_error: field '{field_name}' must be string or null"
        raise StorageSchemaError(msg)