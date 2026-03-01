from __future__ import annotations
"""Agent model override loading.

Why this module exists: role-to-model resolution is pure configuration logic and should
stay independent from runtime graph construction.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_agent_models(
    default_model_name: str,
    config_path: str | None,
) -> dict[str, str]:
    """Why: enforce complete per-role model map with deterministic defaults."""
    resolved = {
        "coordinator": default_model_name,
        "planner": default_model_name,
        "executor": default_model_name,
        "memory": default_model_name,
        "communicator": default_model_name,
    }
    for role, model_name in _load_agent_model_overrides(config_path).items():
        resolved[role] = model_name
    return resolved


def _load_agent_model_overrides(config_path: str | None) -> dict[str, str]:
    """Why: tolerate missing/invalid config and keep runtime boot resilient."""
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        logger.warning("agent_models_config_missing", extra={"path": config_path})
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("agent_models_config_invalid_json", extra={"path": config_path})
        return {}

    if not isinstance(raw, dict):
        logger.warning("agent_models_config_invalid_shape", extra={"path": config_path})
        return {}

    output: dict[str, str] = {}
    for role in ("coordinator", "planner", "executor", "memory", "communicator"):
        value = raw.get(role)
        if isinstance(value, str) and value.strip():
            output[role] = value.strip()
    return output
