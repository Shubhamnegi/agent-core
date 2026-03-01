# agent-core
Core service to handle agentic orchestration.

# Language
- Python
- Core dependency
    - ADK
    - FAST MCP
    - FAST API
- Dependency manager uv

# Documentation

- [Architecture source](./agent_architecture_v3.docx.md)
- [Docs index](./docs/README.md)
- [Implementation checklist](./docs/06-implementation-checklist.md)
- [ADK-only migration checklist](./docs/11-adk-only-migration-checklist.md)
- [Coding standards](./docs/07-coding-standards.md)

# Scaffold structure

- `src/agent_core/domain`: core models and typed exceptions
- `src/agent_core/application`: repository/adapter ports
- `src/agent_core/infra`: ADK runtime, adapters, config, logging
- `src/agent_core/api`: FastAPI endpoints and request schemas
- `src/agent_core/prompts`: centralized agent prompt templates/constants

# Latest update

- Runtime execution is ADK-only for `POST /agent/run`.
- Legacy custom orchestrator and mock planner/executor flow were removed.
- Planner and executor are both ADK sub-agents under the ADK coordinator graph.

# Quick start (uv + venv)

```bash
uv venv .venv
source .venv/bin/activate
uv sync
uv run uvicorn agent_core.api.main:app --reload
```

# Validate

```bash
uv run ruff check .
uv run mypy .
uv run pytest -q
```

# External services (docker compose)

```bash
cp .env.example .env
docker compose up --build
```

Services started:
- Agent API: `http://localhost:8000`
- Redis (queue/message bus base): `localhost:6379`
- OpenSearch (db/storage): `http://localhost:9200`
- Mock skill service: `http://localhost:8081`

## Storage backend

- Default backend: in-memory (`AGENT_STORAGE_BACKEND=in_memory`)
- OpenSearch backend: set `AGENT_STORAGE_BACKEND=opensearch`
- OpenSearch adapter initializes strict mappings for:
    - `agent_memory`, `agent_souls`, `agent_sessions`, `agent_plans`, `agent_events`
- `agent_events` index is configured with ILM retention policy
- `agent_memory` includes KNN vector mapping + tenant/scope pre-filter query support
- Embeddings are generated through ADK helper utilities (`google.adk.tools.spanner.utils.embed_contents_async`)
- Required embedding envs for OpenSearch KNN:
    - `AGENT_EMBEDDING_MODEL_NAME` (example: `models/text-embedding-004`)
    - `AGENT_EMBEDDING_OUTPUT_DIMENSIONALITY` (optional override)
    - `AGENT_OPENSEARCH_EMBEDDING_DIMS` must match generated vector length

## MCP configuration (JSON)

- Default MCP registry file: `config/mcp_config.json` (override with `AGENT_MCP_CONFIG_PATH`)
- Per-endpoint transport is configured in JSON via `transport` (`streamable_http` or `sse`).
- For remote MCP services, prefer `streamable_http` (ADK deployment pattern recommendation).
- MCP endpoint auth is resolved per incoming `POST /agent/run` request.
- For `skill_service`, API key resolution order is:
    1. `X-Skill-Service-Key` request header
    2. `AGENT_SKILL_SERVICE_KEY` env fallback

Gemini runtime env:
- `AGENT_MODEL_NAME` (default: `models/gemini-flash-lite-latest`)
- `AGENT_MODELS_CONFIG_PATH` (default: `config/agent_models.json`)
    - Optional per-role overrides using keys: `coordinator`, `planner`, `executor`, `memory`, `communicator`
    - Example: set executor to `gemini-2.5-flash-lite` while planner/coordinator stay on stronger models
- `GOOGLE_API_KEY`

## Communication subagent configuration

- Config file path: `AGENT_COMMUNICATION_CONFIG_PATH` (default: `config/communication_config.json`)
- Slack token source: `slack.bot_token_env` in config (default env key: `SLACK_BOT_TOKEN`)
- SMTP password source: `smtp.password_env` in config (default env key: `SMTP_PASSWORD`)
- Orchestrator can delegate communication tasks to `communicator_subagent_d` for:
    - `send_slack_message` (text/blocks + optional file)
    - `read_slack_messages` (message stream + attached file metadata)
    - `send_email_smtp` (preconfigured SMTP email, with optional attachments)
