from __future__ import annotations

import json
import sys
import urllib.request


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: inspect_plan_events.py <plan_id>")
        return 2

    plan_id = sys.argv[1]
    url = "http://localhost:9200/lt_agent__agent_events/_search"
    body = {
        "size": 500,
        "query": {"bool": {"filter": [{"term": {"plan_id": plan_id}}]}},
        "sort": [{"ts": {"order": "asc"}}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    obj = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    hits = obj.get("hits", {}).get("hits", [])

    authors: set[str] = set()
    planner_calls: list[str] = []
    executor_calls: list[str] = []
    memory_calls: list[str] = []
    planner_tools: list[str] = []
    memory_write_seen = False
    memory_write_statuses: list[str] = []
    memory_write_reasons: list[str] = []
    orchestrator_prompt_has_memory_clause = False
    planner_prompt_has_memory_search_clause = False
    orchestrator_instruction_preview = ""
    planner_instruction_preview = ""
    orchestrator_content_preview = ""
    planner_content_preview = ""

    for hit in hits:
        src = hit.get("_source", {})
        payload = src.get("payload", {}) or {}
        author = payload.get("author") or payload.get("agent")
        if isinstance(author, str) and author:
            authors.add(author)

        if src.get("event_type") == "adk.prompt" and author == "planner_subagent_a":
            candidate_tools = payload.get("available_tools", [])
            if isinstance(candidate_tools, list):
                planner_tools = candidate_tools
            if not planner_content_preview:
                texts = payload.get("content_texts") or []
                if isinstance(texts, list) and texts:
                    planner_content_preview = str(texts[-1])[:300]
            system_instruction = payload.get("system_instruction")
            if isinstance(system_instruction, str):
                if not planner_instruction_preview:
                    planner_instruction_preview = system_instruction[:300]
                planner_prompt_has_memory_search_clause = (
                    "search_relevant_memory" in system_instruction
                )

        if src.get("event_type") == "adk.prompt" and author == "orchestrator_manager":
            if not orchestrator_content_preview:
                texts = payload.get("content_texts") or []
                if isinstance(texts, list) and texts:
                    orchestrator_content_preview = str(texts[-1])[:300]
            system_instruction = payload.get("system_instruction")
            if isinstance(system_instruction, str):
                if not orchestrator_instruction_preview:
                    orchestrator_instruction_preview = system_instruction[:300]
                orchestrator_prompt_has_memory_clause = (
                    "memory_subagent_c" in system_instruction
                    and "persist durable memory" in system_instruction
                )

        for call in payload.get("function_calls") or []:
            name = call.get("name")
            if not isinstance(name, str):
                continue
            if author == "planner_subagent_a":
                planner_calls.append(name)
            elif author == "executor_subagent_b":
                executor_calls.append(name)
            elif author == "memory_subagent_c":
                memory_calls.append(name)

        for response in payload.get("function_responses") or []:
            name = response.get("name")
            if name in {"write_memory", "save_action_memory", "save_user_memory"}:
                memory_write_seen = True
                inner: dict[str, Any] | None = None
                if isinstance(response.get("response"), dict):
                    inner = response["response"]
                elif isinstance(response.get("response_json"), str):
                    try:
                        parsed = json.loads(response["response_json"])
                        if isinstance(parsed, dict):
                            inner = parsed
                    except Exception:
                        inner = None
                if isinstance(inner, dict):
                    status = inner.get("status")
                    reason = inner.get("reason")
                    if isinstance(status, str):
                        memory_write_statuses.append(status)
                    if isinstance(reason, str):
                        memory_write_reasons.append(reason)

    print("events", len(hits))
    print("authors", sorted(authors))
    print("planner_tools", planner_tools)
    print("planner_calls", planner_calls)
    print("executor_calls", sorted(set(executor_calls)))
    print("memory_calls", memory_calls)
    print("memory_write_seen", memory_write_seen)
    print("memory_write_statuses", memory_write_statuses)
    print("memory_write_reasons", sorted(set(memory_write_reasons)))
    print("orchestrator_prompt_has_memory_clause", orchestrator_prompt_has_memory_clause)
    print("planner_prompt_has_memory_search_clause", planner_prompt_has_memory_search_clause)
    print("orchestrator_instruction_preview", orchestrator_instruction_preview)
    print("planner_instruction_preview", planner_instruction_preview)
    print("orchestrator_content_preview", orchestrator_content_preview)
    print("planner_content_preview", planner_content_preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
