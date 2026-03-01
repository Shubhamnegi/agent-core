from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "agent-core"
    environment: str = "local"
    log_level: str = "INFO"
    model_name: str = "models/gemini-flash-lite-latest"
    max_plan_steps: int = 10
    max_replans: int = 3
    storage_backend: str = "in_memory"

    opensearch_url: str = "http://localhost:9200"
    opensearch_index_prefix: str = ""
    opensearch_verify_certs: bool = False
    opensearch_embedding_dims: int = 768
    opensearch_events_retention_days: int = 30
    embedding_model_name: str = "models/text-embedding-004"
    embedding_output_dimensionality: int | None = None
    models_config_path: str | None = "config/agent_models.json"
    redis_url: str = "redis://localhost:6379/0"
    skill_service_url: str = "http://localhost:8081"
    skill_service_key: str | None = None
    communication_config_path: str | None = "config/communication_config.json"
    mcp_config_path: str | None = "config/mcp_config.json"
    mcp_session_timeout: float = 60.0

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")
