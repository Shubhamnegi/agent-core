from __future__ import annotations
"""MCP endpoint resolution utilities for runtime wiring.

Why this module exists: endpoint/env fallback logic is configuration-heavy and distracts
from execution flow when kept inside the runtime orchestrator.
"""

import os
from typing import Any

from agent_core.infra.adk.mcp import ResolvedMcpEndpoint, load_mcp_config


def _default_skill_service_endpoint() -> dict[str, Any]:
    return {
        "name": "skill_service",
        "url_env": "AGENT_SKILL_SERVICE_URL",
        "planner_tool_filter": ["find_relevant_skill", "load_instructions"],
        "auth_headers": [
            {
                "name": "x-api-key",
                "request_header": "x-skill-service-key",
                "env": "AGENT_SKILL_SERVICE_KEY",
            }
        ],
    }


def _find_endpoint_by_name(config: dict[str, Any], endpoint_name: str) -> dict[str, Any] | None:
    endpoints = config.get("endpoints", [])
    if not isinstance(endpoints, list):
        return None
    for endpoint in endpoints:
        if isinstance(endpoint, dict) and endpoint.get("name") == endpoint_name:
            return endpoint
    return None


def _build_runtime_env_overrides(
    skill_service_url: str | None,
    skill_service_key: str | None,
) -> dict[str, str]:
    values = dict(os.environ)
    if skill_service_url:
        values["AGENT_SKILL_SERVICE_URL"] = skill_service_url
    if skill_service_key:
        values["AGENT_SKILL_SERVICE_KEY"] = skill_service_key
    return values


def _get_endpoint_name(config: dict[str, Any]) -> str:
    endpoint_name = config.get("planner_endpoint")
    if isinstance(endpoint_name, str) and endpoint_name:
        return endpoint_name
    return "skill_service"


def _select_endpoint_config(
    mcp_config_path: str | None,
    env_values: dict[str, str],
) -> dict[str, Any]:
    """Why: prefer explicit config, but allow env-based fallback for local/dev setups."""
    if mcp_config_path:
        config = load_mcp_config(mcp_config_path)
        endpoint_name = _get_endpoint_name(config)
        endpoint = _find_endpoint_by_name(config, endpoint_name)
        if endpoint is None:
            msg = "mcp_endpoint_not_found"
            raise ValueError(msg)
        return endpoint

    fallback = _default_skill_service_endpoint()
    if not env_values.get("AGENT_SKILL_SERVICE_URL"):
        return {}
    return fallback


def _load_mcp_config_or_fallback(
    mcp_config_path: str | None,
    env_values: dict[str, str],
) -> dict[str, Any]:
    """Why: executor path needs full config shape, even when using fallback endpoint."""
    if mcp_config_path:
        return load_mcp_config(mcp_config_path)

    fallback_endpoint = _default_skill_service_endpoint()
    if not env_values.get("AGENT_SKILL_SERVICE_URL"):
        return {}
    return {
        "planner_endpoint": fallback_endpoint["name"],
        "endpoints": [fallback_endpoint],
    }


def _normalize_headers(request_headers: dict[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in request_headers.items()}


def _endpoint_debug(endpoint: ResolvedMcpEndpoint | None) -> dict[str, Any] | None:
    if endpoint is None:
        return None
    return {
        "name": endpoint.name,
        "transport": endpoint.transport,
        "url": endpoint.url,
        "command": endpoint.command,
        "args": endpoint.args,
        "planner_tools": endpoint.planner_tools,
        "header_names": sorted(endpoint.headers.keys()),
    }
