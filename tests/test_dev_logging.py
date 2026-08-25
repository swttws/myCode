import asyncio
import logging

from mycode.dev_logging import configure_dev_logging
from mycode.log_context import use_log_identity


def test_dev_logging_injects_async_log_identity_context(tmp_path):
    log_file = tmp_path / "dev.log"
    configure_dev_logging(log_file)

    logger = logging.getLogger("mycode.agent.loop")
    with use_log_identity(
        agent_role="member",
        team_name="team-alpha",
        member_name="backend-dev",
        task_id="task-1",
        batch_id="batch-1",
    ):
        logger.info("deep agent event")

    content = log_file.read_text(encoding="utf-8")
    assert "角色=member" in content
    assert "团队=team-alpha" in content
    assert "成员=backend-dev" in content
    assert "任务=task-1" in content
    assert "批次=batch-1" in content


def test_dev_logging_explicit_extra_overrides_context(tmp_path):
    log_file = tmp_path / "dev.log"
    configure_dev_logging(log_file)
    logger = logging.getLogger("mycode.tool.executor")

    with use_log_identity(agent_role="member", member_name="context-member"):
        logger.info(
            "explicit identity",
            extra={"agent_role": "lead", "member_name": "explicit-member"},
        )

    content = log_file.read_text(encoding="utf-8")
    assert "角色=lead" in content
    assert "成员=explicit-member" in content
    assert "成员=context-member" not in content


def test_dev_logging_context_isolated_between_async_tasks(tmp_path):
    log_file = tmp_path / "dev.log"
    configure_dev_logging(log_file)
    logger = logging.getLogger("mycode.tool.executor")

    async def emit(member_name: str) -> None:
        with use_log_identity(
            agent_role="member",
            team_name="team-alpha",
            member_name=member_name,
        ):
            await asyncio.sleep(0)
            logger.info("concurrent event")

    async def run_all() -> None:
        await asyncio.gather(emit("backend-dev"), emit("tester"))

    asyncio.run(run_all())

    lines = log_file.read_text(encoding="utf-8").splitlines()
    events = [line for line in lines if "concurrent event" in line]
    assert len(events) == 2
    assert any("成员=backend-dev" in line for line in events)
    assert any("成员=tester" in line for line in events)


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

    assert "代理类型=subagent" in content
    assert "角色名称=explore" in content
    assert "任务=task-000001" in content


def test_dev_logging_includes_team_context_fields(tmp_path):
    log_file = tmp_path / "dev.log"

    configure_dev_logging(log_file)

    logger = logging.getLogger("mycode.team.runtime")
    logger.info(
        "team event",
        extra={
            "agent_type": "subagent",
            "role_name": "worker",
            "task_id": "task-000002",
            "team_name": "team-alpha",
            "member_name": "member-1",
            "batch_id": "batch-7",
            "action": "execute",
            "phase": "running",
            "state": "active",
            "duration_ms": 42,
            "reason_code": "team_ready",
            "path": "/tmp/team-alpha/member-1",
        },
    )

    content = log_file.read_text(encoding="utf-8")

    assert "代理类型=subagent" in content
    assert "角色名称=worker" in content
    assert "任务=task-000002" in content
    assert "团队=team-alpha" in content
    assert "成员=member-1" in content
    assert "批次=batch-7" in content
    assert "操作=execute" in content
    assert "阶段=运行中" in content
    assert "状态=活动" in content
    assert "耗时毫秒=42" in content
    assert "原因码=team_ready" in content
    assert "路径=/tmp/team-alpha/member-1" in content


def test_dev_logging_includes_tool_context_without_sensitive_fields(tmp_path):
    log_file = tmp_path / "dev.log"

    configure_dev_logging(log_file)

    logger = logging.getLogger("mycode.team.lead_tools")
    logger.info(
        "团队工具事件",
        extra={
            "team_name": "team-alpha",
            "tool_name": "team_member_spawn",
            "message_id": "msg-1",
            "protocol": "message",
            "error_code": "phase_not_ready",
            "prompt": "FULL_PROMPT_SECRET",
            "task_body": "FULL_TASK_SECRET",
            "environment": {"OPENAI_API_KEY": "ENV_SECRET"},
            "secret": "TOKEN_SECRET",
        },
    )

    content = log_file.read_text(encoding="utf-8")

    assert "工具=team_member_spawn" in content
    assert "消息=msg-1" in content
    assert "协议=message" in content
    assert "错误码=phase_not_ready" in content
    assert "FULL_PROMPT_SECRET" not in content
    assert "FULL_TASK_SECRET" not in content
    assert "ENV_SECRET" not in content
    assert "TOKEN_SECRET" not in content


def test_dev_logging_renders_team_event_and_phase_in_chinese(tmp_path):
    log_file = tmp_path / "dev.log"
    configure_dev_logging(log_file)
    logger = logging.getLogger("mycode.team.service")
    logger.info(
        "team.member.spawn.completed",
        extra={"team_name": "team-alpha", "phase": "dispatch_ready", "state": "active"},
    )

    content = log_file.read_text(encoding="utf-8")
    assert "事件=团队·成员·派生·已完成" in content
    assert "事件码=team.member.spawn.completed" in content
    assert "阶段=可派生成员" in content
    assert "状态=活动" in content


def test_dev_logging_renders_core_team_identity_and_result_in_chinese(tmp_path):
    log_file = tmp_path / "dev.log"
    configure_dev_logging(log_file)
    logger = logging.getLogger("mycode.team.runtime")
    logger.info(
        "team.task.result",
        extra={
            "agent_role": "member",
            "team_name": "team-alpha",
            "member_name": "dev",
            "task_id": "task-1",
            "batch_id": "batch-1",
            "event_id": "event-1",
            "message_id": "message-1",
            "task_summary": "新增幂等测试",
            "result_summary": "测试通过",
        },
    )

    content = log_file.read_text(encoding="utf-8")

    assert "角色=member" in content
    assert "团队=team-alpha" in content
    assert "成员=dev" in content
    assert "任务=task-1" in content
    assert "事件编号=event-1" in content
    assert "消息=message-1" in content
    assert "任务摘要=新增幂等测试" in content
    assert "结果=测试通过" in content
    assert "事件=任务返回结果" in content
    assert "agent_role=" not in content
    assert "team_name=" not in content
    assert "event_code=" not in content


def test_dev_logging_hides_team_internal_noise_but_keeps_task_events(tmp_path):
    log_file = tmp_path / "dev.log"
    configure_dev_logging(log_file)
    logger = logging.getLogger("mycode.team.runtime")
    logger.info("team.lock.acquire.started")
    logger.info("team.runtime.idle")
    logger.info("team.task.started", extra={"task_id": "task-1"})

    content = log_file.read_text(encoding="utf-8")

    assert "team.lock.acquire.started" not in content
    assert "team.runtime.idle" not in content
    assert "任务开始执行" in content
