from __future__ import annotations

import ast
import json
import multiprocessing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile, gettempdir
from typing import Any

DEFAULT_LARGE_RESPONSE_THRESHOLD_BYTES = 50 * 1024
DEFAULT_EXEC_PYTHON_TIMEOUT_SECONDS = 30
DEFAULT_EXEC_PYTHON_OUTPUT_LIMIT_BYTES = 500 * 1024

_TEMP_FILE_REGISTRY: dict[str, datetime] = {}
_AGENT_EVENTS: list[dict[str, Any]] = []


def write_temp(data: str) -> dict[str, str]:
    """Write raw text payload to a temporary file and return its file id/path.

    Use before incremental reads or constrained script extraction on large outputs.
    """
    with NamedTemporaryFile(mode="w", suffix=".tmp", delete=False) as temp_file:
        temp_file.write(data)
        file_id = temp_file.name
    _TEMP_FILE_REGISTRY[file_id] = datetime.now(UTC)
    return {"file_id": file_id}


def read_lines(file_id: str, start: int, n: int) -> dict[str, Any]:
    """Read a slice of lines from a temp file.

    Use to inspect large payloads without loading full content into prompt context.
    """
    path = Path(file_id)
    if not path.exists():
        return {"lines": []}
    with path.open("r") as handle:
        rows = handle.readlines()
    return {"lines": [line.rstrip("\n") for line in rows[start : start + n]]}


def exec_python(
    script: str,
    file_id: str,
    timeout_seconds: int = DEFAULT_EXEC_PYTHON_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_EXEC_PYTHON_OUTPUT_LIMIT_BYTES,
) -> dict[str, Any]:
    """Run a sandboxed Python extraction script against a temp JSON file.

    Use for controlled transformation of large responses to a compact result.
    Script must assign final data to variable `result`.
    """
    script_hash = sha256(script.encode("utf-8")).hexdigest()
    queue: Any = multiprocessing.Queue(maxsize=1)
    process = multiprocessing.Process(
        target=_exec_python_worker,
        args=(script, file_id, max_output_bytes, queue),
    )
    process.start()
    process.join(timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join()
        return {
            "status": "failed",
            "reason": "exec_python_timeout",
            "script_hash": script_hash,
            "file_id": file_id,
        }

    result = (
        queue.get()
        if not queue.empty()
        else {"status": "failed", "reason": "exec_python_failed"}
    )
    if not isinstance(result, dict):
        result = {"status": "failed", "reason": "exec_python_invalid_result"}

    enriched = {**result, "script_hash": script_hash, "file_id": file_id}
    if enriched.get("status") == "ok":
        _append_agent_event(
            event_type="large_response.exec_python",
            payload={"script_hash": script_hash, "strategy": "write_temp_read_lines_exec_python"},
        )
    return enriched


def handle_large_response(
    response: str,
    return_spec: dict[str, Any],
    extraction_script: str | None = None,
    threshold_bytes: int = DEFAULT_LARGE_RESPONSE_THRESHOLD_BYTES,
    timeout_seconds: int = DEFAULT_EXEC_PYTHON_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_EXEC_PYTHON_OUTPUT_LIMIT_BYTES,
) -> dict[str, Any]:
    """Normalize large model output into required fields with safe fallback strategy.

    Use this when response size may exceed prompt/tool limits.
    Automatically selects direct projection for small payloads and temp-file
    extraction for large payloads.
    """
    response_size = len(response.encode("utf-8"))
    required_fields = list(return_spec.keys())
    if response_size < threshold_bytes:
        direct = _project_direct_response(response, required_fields)
        return {
            "status": "ok",
            "strategy": "direct",
            "large_response": False,
            "data": direct,
            "content_length": response_size,
        }

    temp_result = write_temp(response)
    file_id = temp_result["file_id"]
    sample = read_lines(file_id, 0, 20)
    script = extraction_script or _default_extraction_script(required_fields)

    execution = exec_python(
        script=script,
        file_id=file_id,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )
    cleanup_temp_file(file_id)

    if execution.get("status") != "ok":
        return {
            "status": "failed",
            "strategy": "write_temp_read_lines_exec_python",
            "large_response": True,
            "content_length": response_size,
            "sample": sample.get("lines", []),
            "script_hash": execution.get("script_hash"),
            "reason": execution.get("reason", "exec_python_failed"),
        }

    extracted = execution.get("result")
    if not _matches_required_fields(extracted, required_fields):
        return {
            "status": "failed",
            "strategy": "write_temp_read_lines_exec_python",
            "large_response": True,
            "content_length": response_size,
            "sample": sample.get("lines", []),
            "script_hash": execution.get("script_hash"),
            "reason": "extraction_contract_violation",
        }

    return {
        "status": "ok",
        "strategy": "write_temp_read_lines_exec_python",
        "large_response": True,
        "content_length": response_size,
        "sample": sample.get("lines", []),
        "script_hash": execution.get("script_hash"),
        "data": extracted,
    }


def cleanup_temp_file(file_id: str) -> bool:
    """Delete a temporary file by file id/path and remove registry entry."""
    path = Path(file_id)
    removed = False
    if path.exists():
        path.unlink()
        removed = True
    _TEMP_FILE_REGISTRY.pop(file_id, None)
    return removed


def sweep_temp_files(max_age_seconds: int = 300) -> dict[str, Any]:
    """Delete tracked temp files older than `max_age_seconds`."""
    now = datetime.now(UTC)
    removed: list[str] = []
    failed: list[str] = []

    for file_id, created_at in list(_TEMP_FILE_REGISTRY.items()):
        age_seconds = int((now - created_at).total_seconds())
        if age_seconds < max_age_seconds:
            continue
        try:
            cleanup_temp_file(file_id)
            removed.append(file_id)
        except OSError:
            failed.append(file_id)

    return {"removed": removed, "failed": failed}


def list_agent_events() -> list[dict[str, Any]]:
    """Return in-memory tool events emitted by large-response helpers."""
    return list(_AGENT_EVENTS)


def reset_tool_state() -> None:
    """Clear tracked temp files and in-memory tool event log."""
    for file_id in list(_TEMP_FILE_REGISTRY.keys()):
        cleanup_temp_file(file_id)
    _AGENT_EVENTS.clear()


def _append_agent_event(event_type: str, payload: dict[str, Any]) -> None:
    _AGENT_EVENTS.append(
        {
            "event_type": event_type,
            "payload": payload,
            "ts": datetime.now(UTC).isoformat(),
        }
    )


def _default_extraction_script(required_fields: list[str]) -> str:
    projected_fields = ", ".join(
        [f"\"{field}\": payload.get(\"{field}\")" for field in required_fields]
    )
    return (
        "payload = read_json_file(file_id)\n"
        f"result = {{{projected_fields}}}"
    )


def _project_direct_response(response: str, required_fields: list[str]) -> dict[str, Any]:
    parsed = _try_parse_json_object(response)
    if parsed is None:
        if len(required_fields) == 1:
            return {required_fields[0]: response}
        return {}
    return {field: parsed.get(field) for field in required_fields if field in parsed}


def _try_parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _matches_required_fields(data: Any, required_fields: list[str]) -> bool:
    if not isinstance(data, dict):
        return False
    return set(data.keys()) == set(required_fields)


def _exec_python_worker(
    script: str,
    file_id: str,
    max_output_bytes: int,
    queue: Any,
) -> None:
    try:
        _validate_script(script)
        resolved_file = Path(file_id).resolve()
        temp_root = Path(gettempdir()).resolve()
        if temp_root not in resolved_file.parents:
            queue.put({"status": "failed", "reason": "exec_python_file_outside_tempdir"})
            return

        safe_globals = {
            "__builtins__": {
                "len": len,
                "min": min,
                "max": max,
                "sum": sum,
                "range": range,
                "enumerate": enumerate,
                "zip": zip,
                "sorted": sorted,
                "list": list,
                "dict": dict,
                "set": set,
                "tuple": tuple,
                "int": int,
                "float": float,
                "str": str,
                "bool": bool,
                "abs": abs,
                "all": all,
                "any": any,
            },
            "json": json,
            "file_id": str(resolved_file),
            "read_json_file": _read_json_file,
        }
        local_vars: dict[str, Any] = {}
        compiled = compile(script, "<exec_python>", "exec")
        exec(compiled, safe_globals, local_vars)
        if "result" not in local_vars:
            queue.put({"status": "failed", "reason": "exec_python_missing_result"})
            return

        result = local_vars["result"]
        encoded = json.dumps(result).encode("utf-8")
        if len(encoded) > max_output_bytes:
            queue.put({"status": "failed", "reason": "exec_python_output_too_large"})
            return

        queue.put({"status": "ok", "result": result})
    except Exception as exc:  # pragma: no cover
        queue.put({"status": "failed", "reason": f"exec_python_error:{exc}"})


def _validate_script(script: str) -> None:
    tree = ast.parse(script)
    banned_calls = {
        "open",
        "exec",
        "eval",
        "compile",
        "input",
        "__import__",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
    }
    banned_nodes = (ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith)
    for node in ast.walk(tree):
        if isinstance(node, banned_nodes):
            msg = "exec_python_disallowed_syntax"
            raise ValueError(msg)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in banned_calls:
                msg = "exec_python_disallowed_call"
                raise ValueError(msg)


def _read_json_file(file_path: str) -> dict[str, Any]:
    with Path(file_path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        msg = "exec_python_json_must_be_object"
        raise ValueError(msg)
    return payload
