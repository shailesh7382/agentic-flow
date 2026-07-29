import json
import logging

from app.config import Settings
from app.logging_config import (
    configure_logging,
    log_event,
    reset_node,
    reset_request_id,
    reset_run_id,
    set_node,
    set_request_id,
    set_run_id,
)


def test_json_logging_correlates_and_rotates(tmp_path):
    log_path = tmp_path / "agent.log"
    settings = Settings(
        log_file=str(log_path),
        log_level="DEBUG",
        log_max_bytes=1024,
        log_backup_count=2,
        log_include_content=False,
    )
    configure_logging(settings)
    request_token = set_request_id("request-test")
    run_token = set_run_id("run-test")
    node_token = set_node("plan")
    try:
        for index in range(40):
            log_event(
                logging.getLogger("agentic_flow.test"),
                logging.DEBUG,
                "test.rotation",
                "Writing enough structured data to exercise rotation",
                index=index,
                payload="x" * 180,
            )
    finally:
        reset_node(node_token)
        reset_run_id(run_token)
        reset_request_id(request_token)

    for handler in logging.getLogger().handlers:
        handler.flush()

    files = sorted(tmp_path.glob("agent.log*"))
    records = [
        json.loads(line)
        for file in files
        for line in file.read_text(encoding="utf-8").splitlines()
        if line
    ]

    assert log_path.exists()
    assert (tmp_path / "agent.log.1").exists()
    assert any(
        record["event"] == "test.rotation"
        and record["request_id"] == "request-test"
        and record["run_id"] == "run-test"
        and record["agent_node"] == "plan"
        for record in records
    )
