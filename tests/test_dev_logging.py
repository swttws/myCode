import logging

from mycode.dev_logging import configure_dev_logging


def test_dev_logging_includes_subagent_identity_fields(tmp_path):
    log_file = tmp_path / "dev.log"

    configure_dev_logging(log_file)

    logger = logging.getLogger("mycode.subagent.runtime")
    logger.info(
        "subagent event",
        extra={
            "agent_type": "subagent",
            "role_name": "explore",
            "task_id": "task-000001",
        },
    )

    content = log_file.read_text(encoding="utf-8")

    assert "agent_type=subagent" in content
    assert "role_name=explore" in content
    assert "task_id=task-000001" in content
