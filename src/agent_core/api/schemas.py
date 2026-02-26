from pydantic import BaseModel, Field


class AgentRunPayload(BaseModel):
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    stream: bool = False


class AgentRunResult(BaseModel):
    status: str
    response: str
    plan_id: str


class SoulPayload(BaseModel):
    user_id: str | None = None
    persona: dict
    policies: dict | None = None


class MemoryQueryPayload(BaseModel):
    tenant_id: str
    user_id: str
    query_text: str
    top_k: int = 5
    scope: str = "session"
