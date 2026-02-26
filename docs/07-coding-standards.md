# 07 — Coding Standards (Python + uv)

## Objective

Define engineering standards for Agent Core implementation with Python and `uv` dependency management.

## 1) Dependency and environment management (`uv`)

- Use `uv` as the only dependency manager.
- Keep dependencies in `pyproject.toml`.
- Use lockfiles for reproducible builds (`uv.lock`).
- Add runtime dependencies with:
  - `uv add <package>`
- Add dev dependencies with:
  - `uv add --dev <package>`
- Sync environment with:
  - `uv sync`
- Run tools through `uv`:
  - `uv run pytest`
  - `uv run ruff check .`
  - `uv run ruff format .`

## 2) Python version and project settings

- Target Python 3.11+.
- Set `requires-python` in `pyproject.toml`.
- Keep configuration centralized in `pyproject.toml` where possible.

## 3) Code style and quality

- Follow PEP 8 and type-hint all public functions.
- Prefer explicit, descriptive names over short abbreviations.
- Keep modules focused; one responsibility per module.
- Favor composition over deep inheritance.
- Avoid hidden global state in orchestration components.
- Comments only why not what. 
- Clear logging to easily trace with request id tracing.

## 4) Linting, formatting, and typing

- Formatter: `ruff format`.
- Linter: `ruff check`.
- Type checking: `mypy` (recommended for service boundaries and contracts).
- CI must fail on lint/type/test errors.

Suggested dev dependencies:
- `ruff`
- `pytest`
- `pytest-asyncio`
- `mypy`

## 5) Testing standards

- Write unit tests for pure logic (planning validation, memory contracts).
- Write integration tests for orchestration flows and replanning.
- Include failure-path tests (timeouts, schema mismatches, lock contention).
- Keep tests deterministic and isolated from external network dependencies.

## 6) Architecture-specific standards

### Contract-first design
- Define `return_spec` and schemas before implementing execution logic.
- Validate all external IO at boundaries (Storage Adapter, Skill calls).

### Boundary preservation
- Chat API must not orchestrate subagents.
- AgentCore must not bypass Storage Adapter.
- SubAgent-B must never call unlisted skills.

### Event completeness
- Emit structured events for lifecycle, skill calls, memory writes, failures.
- Include identifiers: `tenant_id`, `session_id`, `plan_id`, `task_id`, timestamp.

## 7) Error handling and reliability

- Use typed exceptions (`StorageSchemaError`, `MemoryLockError`, etc.).
- Return structured failures for non-recoverable paths.
- Enforce max replan attempts to avoid infinite loops.
- Preserve completed work during retries/replans.

## 8) Security and compliance

- Never log secrets (`X-Api-Key`, credentials, tokens).
- Enforce tenant isolation in all queries.
- Keep `exec_python` sandboxed with strict limits.
- Validate payload size and schema before persistence.

## 9) Documentation standards

- Keep docs in `docs/` updated with behavior changes.
- Update implementation checklist when scope changes.
- Record API contract changes before deployment.

## 10) Recommended command workflow

- `uv sync`
- `uv run ruff check .`
- `uv run ruff format .`
- `uv run mypy .`
- `uv run pytest`

All pull requests should pass the full workflow before merge.
