from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def write_memory(key: str, data: dict[str, Any], return_spec: dict[str, Any]) -> dict[str, Any]:
    return {"status": "scaffold", "key": key, "validated": bool(return_spec), "data": data}


def read_memory(namespaced_key: str) -> dict[str, Any]:
    return {"status": "scaffold", "key": namespaced_key, "data": None}


def write_temp(data: str) -> dict[str, str]:
    with NamedTemporaryFile(mode="w", suffix=".tmp", delete=False) as temp_file:
        temp_file.write(data)
        file_id = temp_file.name
    return {"file_id": file_id}


def read_lines(file_id: str, start: int, n: int) -> dict[str, Any]:
    path = Path(file_id)
    if not path.exists():
        return {"lines": []}
    with path.open("r") as handle:
        rows = handle.readlines()
    return {"lines": [line.rstrip("\n") for line in rows[start : start + n]]}


def exec_python(script: str, file_id: str) -> dict[str, Any]:
    script_hash = sha256(script.encode("utf-8")).hexdigest()
    return {
        "status": "scaffold",
        "file_id": file_id,
        "script_hash": script_hash,
        "result": "exec_python_scaffold",
    }
