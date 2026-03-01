from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from agent_core.application.ports import MemoryRepository
from agent_core.infra.adapters.embedding import EmbeddingService


@dataclass(slots=True)
class ToolRuntimeContext:
    tenant_id: str
    user_id: str
    session_id: str
    plan_id: str
    memory_repo: MemoryRepository | None
    embedding_service: EmbeddingService | None
    communication_config_path: str | None


_tool_runtime_context: ContextVar[ToolRuntimeContext | None] = ContextVar(
    "adk_tool_runtime_context",
    default=None,
)


def bind_tool_runtime_context(
    tenant_id: str,
    user_id: str,
    session_id: str,
    plan_id: str,
    memory_repo: MemoryRepository | None,
    embedding_service: EmbeddingService | None,
    communication_config_path: str | None = None,
) -> Token[ToolRuntimeContext | None]:
    return _tool_runtime_context.set(
        ToolRuntimeContext(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            plan_id=plan_id,
            memory_repo=memory_repo,
            embedding_service=embedding_service,
            communication_config_path=communication_config_path,
        )
    )


def reset_tool_runtime_context(token: Token[ToolRuntimeContext | None]) -> None:
    _tool_runtime_context.reset(token)


def get_tool_runtime_context() -> ToolRuntimeContext | None:
    return _tool_runtime_context.get()
