FROM ghcr.io/astral-sh/uv:python3.11-bookworm

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --frozen || uv sync

COPY src ./src
COPY README.md ./README.md

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "agent_core.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
