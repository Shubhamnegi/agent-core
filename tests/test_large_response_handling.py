import json

from agent_core.infra.adk.tools import (
    cleanup_temp_file,
    exec_python,
    handle_large_response,
    list_agent_events,
    reset_tool_state,
    sweep_temp_files,
    write_temp,
)


def setup_function() -> None:
    reset_tool_state()


def test_handle_large_response_uses_direct_path_for_small_payload() -> None:
    payload = {"response_text": "hello"}
    result = handle_large_response(
        response=json.dumps(payload),
        return_spec={"response_text": "string"},
        threshold_bytes=1024,
    )

    assert result["status"] == "ok"
    assert result["strategy"] == "direct"
    assert result["large_response"] is False
    assert result["data"] == payload


def test_handle_large_response_runs_pipeline_and_logs_script_hash() -> None:
    payload = {
        "response_text": "x" * 200,
        "extra": "ignored",
    }
    response = json.dumps(payload)

    result = handle_large_response(
        response=response,
        return_spec={"response_text": "string"},
        threshold_bytes=32,
    )

    assert result["status"] == "ok"
    assert result["strategy"] == "write_temp_read_lines_exec_python"
    assert result["large_response"] is True
    assert result["data"] == {"response_text": payload["response_text"]}
    assert isinstance(result["script_hash"], str)

    events = list_agent_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "large_response.exec_python"
    assert events[0]["payload"]["script_hash"] == result["script_hash"]


def test_exec_python_blocks_disallowed_syntax() -> None:
    data_file = write_temp(json.dumps({"response_text": "hello"}))

    result = exec_python(
        script="import os\nresult = {'response_text': 'hello'}",
        file_id=data_file["file_id"],
    )
    cleanup_temp_file(data_file["file_id"])

    assert result["status"] == "failed"
    assert "disallowed" in result["reason"]


def test_exec_python_enforces_timeout() -> None:
    data_file = write_temp(json.dumps({"response_text": "hello"}))

    result = exec_python(
        script="while True:\n    pass",
        file_id=data_file["file_id"],
        timeout_seconds=1,
    )
    cleanup_temp_file(data_file["file_id"])

    assert result["status"] == "failed"
    assert result["reason"] == "exec_python_timeout"


def test_exec_python_enforces_output_size_limit_and_sweeps_temp_files() -> None:
    data_file = write_temp(json.dumps({"response_text": "hello"}))
    result = exec_python(
        script="result = {'response_text': 'x' * 5000}",
        file_id=data_file["file_id"],
        max_output_bytes=128,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "exec_python_output_too_large"

    sweep = sweep_temp_files(max_age_seconds=0)
    assert data_file["file_id"] in sweep["removed"]
