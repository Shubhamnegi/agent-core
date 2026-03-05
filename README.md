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

# Logging (color + format)

- Default console logs are now colorized and human-readable (`AGENT_LOG_FORMAT=pretty`, `AGENT_LOG_COLOR=true`).
- For machine parsing/aggregation, switch to JSON logs:

```bash
AGENT_LOG_FORMAT=json AGENT_LOG_COLOR=false uv run uvicorn agent_core.api.main:app --reload
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

## Event persistence pipeline (Redis + OpenSearch)

When `AGENT_STORAGE_BACKEND=opensearch`, runtime/callback events are not written directly to OpenSearch.
They follow this path:

1. ADK runtime/callback emits `EventRecord`.
2. `RedisStreamEventRepository` publishes the event to Redis Stream `AGENT_EVENTS_STREAM_NAME`.
3. `RedisToOpenSearchEventConsumer` (started with API lifespan) reads from the stream consumer group.
4. Consumer persists into OpenSearch `agent_events` index via `OpenSearchEventRepository`.
5. On success, consumer `XACK`s the stream message.
6. On repeated failures, consumer writes the message to `AGENT_EVENTS_DLQ_STREAM_NAME`.

Delivery semantics:
- At-least-once delivery from Redis Streams.
- Idempotent OpenSearch writes using stable `event_id` as document `_id`.
- `/agent/plans/{plan_id}/trace` reads from OpenSearch and is eventually consistent.

Relevant environment variables:
- `AGENT_REDIS_URL` (default: `redis://localhost:6379/0`)
- `AGENT_EVENTS_STREAM_NAME` (default: `agent.events`)
- `AGENT_EVENTS_STREAM_GROUP` (default: `agent-events-consumers`)
- `AGENT_EVENTS_STREAM_CONSUMER_NAME_PREFIX` (default: `agent-core`)
- `AGENT_EVENTS_STREAM_MAXLEN` (default: `100000`)
- `AGENT_EVENTS_CONSUMER_BATCH_SIZE` (default: `50`)
- `AGENT_EVENTS_CONSUMER_BLOCK_MS` (default: `1000`)
- `AGENT_EVENTS_CONSUMER_RECLAIM_IDLE_MS` (default: `60000`)
- `AGENT_EVENTS_CONSUMER_RECLAIM_COUNT` (default: `50`)
- `AGENT_EVENTS_CONSUMER_MAX_RETRIES` (default: `5`)
- `AGENT_EVENTS_CONSUMER_BACKOFF_SECONDS` (default: `0.2`)
- `AGENT_EVENTS_DLQ_STREAM_NAME` (default: `agent.events.dlq`)

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

### Slack read smoke test

Use this script to verify `read_slack_messages` behavior and quickly debug token/channel issues.

```bash
export SLACK_BOT_TOKEN=...                     # unless configured in communication_config.json
export SLACK_SMOKE_CHANNEL=C0123456789
uv run python scripts/smoke_read_slack_messages.py --limit 10
```

Optional:

```bash
AGENT_COMMUNICATION_CONFIG_PATH=config/communication_config.json \
uv run python scripts/smoke_read_slack_messages.py --channel C0123456789 --no-files
```
