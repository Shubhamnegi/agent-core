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
- [Coding standards](./docs/07-coding-standards.md)

# Scaffold structure

- `src/agent_core/domain`: core models and typed exceptions
- `src/agent_core/application`: ports and orchestrator use-case
- `src/agent_core/infra`: adapters, mock subagents, config, logging
- `src/agent_core/api`: FastAPI endpoints and request schemas

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
