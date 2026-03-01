from __future__ import annotations
"""Session bootstrap and memory indexing helpers.

Why this module exists: session lifecycle concerns are cross-cutting and clearer when
isolated from the main run loop.
"""

from typing import Any

from google.adk.memory import BaseMemoryService
from google.adk.sessions import BaseSessionService

from agent_core.domain.models import AgentRunRequest


def _build_initial_session_state(request: AgentRunRequest) -> dict[str, Any]:
    return {
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "session_id": request.session_id,
    }


async def _ensure_session(
    session_service: BaseSessionService,
    app_name: str,
    request: AgentRunRequest,
) -> bool:
    """Why: return first-turn signal used by trace/memory policy decisions."""
    session = await session_service.get_session(
        app_name=app_name,
        user_id=request.user_id,
        session_id=request.session_id,
    )
    if session is not None:
        return False
    await session_service.create_session(
        app_name=app_name,
        user_id=request.user_id,
        session_id=request.session_id,
        state=_build_initial_session_state(request),
    )
    return True


async def _index_session_in_memory(
    session_service: BaseSessionService,
    memory_service: BaseMemoryService | None,
    app_name: str,
    request: AgentRunRequest,
) -> None:
    """Why: persist session after run so future turns can leverage memory search."""
    if memory_service is None:
        return
    session = await session_service.get_session(
        app_name=app_name,
        user_id=request.user_id,
        session_id=request.session_id,
    )
    if session is None:
        return
    await memory_service.add_session_to_memory(session)
