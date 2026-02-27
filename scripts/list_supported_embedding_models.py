from __future__ import annotations

import argparse
import json
import os
from typing import Any

from google import genai

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - best-effort local convenience
    load_dotenv = None


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(".env", override=True)


def _model_to_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "to_json_dict"):
        value = model.to_json_dict()
        if isinstance(value, dict):
            return value
    if hasattr(model, "model_dump"):
        value = model.model_dump()
        if isinstance(value, dict):
            return value
    if isinstance(model, dict):
        return model
    return {"name": getattr(model, "name", None)}


def _extract_methods(model_payload: dict[str, Any]) -> list[str]:
    candidates = (
        model_payload.get("supported_generation_methods"),
        model_payload.get("supported_actions"),
        model_payload.get("methods"),
        model_payload.get("capabilities"),
    )
    methods: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, list):
            continue
        for item in candidate:
            if isinstance(item, str):
                methods.append(item)
    return sorted(set(methods))


def _supports_embedding(methods: list[str]) -> bool:
    normalized = [method.lower() for method in methods]
    return any("embed" in method for method in normalized)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List models that support embedding (using the same GenAI client stack ADK uses).",
    )
    parser.add_argument("--all", action="store_true", help="Print all models, not just embedding-capable")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument("--limit", type=int, default=500, help="Maximum models to inspect")
    arguments = parser.parse_args()

    _load_env()

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("GOOGLE_API_KEY is missing. Load it in .env or shell environment.")
        return 2

    client = genai.Client(api_key=api_key)

    inspected: list[dict[str, Any]] = []
    for index, model in enumerate(client.models.list()):
        if index >= arguments.limit:
            break
        payload = _model_to_dict(model)
        name = payload.get("name") or payload.get("display_name") or "unknown"
        methods = _extract_methods(payload)
        inspected.append(
            {
                "name": name,
                "supports_embedding": _supports_embedding(methods),
                "methods": methods,
            }
        )

    filtered = [row for row in inspected if row["supports_embedding"]]
    output_rows = inspected if arguments.all else filtered

    if arguments.json:
        print(json.dumps(output_rows, indent=2))
        return 0

    print(f"inspected_models={len(inspected)}")
    print(f"embedding_capable_models={len(filtered)}")
    if not output_rows:
        print("No matching models found.")
        return 1

    for row in output_rows:
        methods_text = ", ".join(row["methods"]) if row["methods"] else "(no methods reported)"
        print(f"- {row['name']}: {methods_text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
