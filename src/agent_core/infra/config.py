from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "agent-core"
    environment: str = "local"
    log_level: str = "INFO"
    log_format: str = "pretty"
    log_color: bool = True
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
    events_stream_name: str = "agent.events"
    events_stream_group: str = "agent-events-consumers"
    events_stream_consumer_name_prefix: str = "agent-core"
    events_stream_maxlen: int = 100000
    events_consumer_batch_size: int = 50
    events_consumer_block_ms: int = 1000
    events_consumer_reclaim_idle_ms: int = 60000
    events_consumer_reclaim_count: int = 50
    events_consumer_max_retries: int = 5
    events_consumer_backoff_seconds: float = 0.2
    events_dlq_stream_name: str = "agent.events.dlq"
    skill_service_url: str = "http://localhost:8081"
    skill_service_key: str | None = None
    communication_config_path: str | None = "config/communication_config.json"
    mcp_config_path: str | None = "config/mcp_config.json"
    mcp_session_timeout: float = 60.0

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")
