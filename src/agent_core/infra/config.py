from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "agent-core"
    environment: str = "local"
    log_level: str = "INFO"
    runtime_engine: str = "adk_scaffold"
    model_name: str = "models/gemini-flash-lite-latest"
    max_plan_steps: int = 10
    max_replans: int = 3

    opensearch_url: str = "http://localhost:9200"
    redis_url: str = "redis://localhost:6379/0"
    skill_service_url: str = "http://localhost:8081"
    skill_service_key: str | None = None
    mcp_config_path: str | None = "config/mcp_config.json"

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")
