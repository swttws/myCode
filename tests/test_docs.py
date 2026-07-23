import re
from pathlib import Path

import yaml

from mycode.config import load_config
from mycode.mcp import MCPTransportKind, load_mcp_config


def test_readme_documents_supported_protocols_and_agent_scope():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Stage 03" in readme
    assert "anthropic" in readme
    assert "openai_responses" in readme
    assert "openai_chat" in readme
    assert "thinking" in readme
    assert "read_file" in readme
    assert "write_file" in readme
    assert "edit_file" in readme
    assert "run_command" in readme
    assert "find_files" in readme
    assert "search_code" in readme
    assert "shell" in readme
    assert "Agent Loop" in readme
    assert "事件流" in readme
    assert "工具分批" in readme
    assert "plan-only" in readme
    assert "取消" in readme
    assert "超时" in readme
    assert "Stage 05" in readme
    assert "Agent 递归调用" in readme
    assert "Anthropic 工具调用" in readme


def test_example_configs_exist_and_use_environment_variables():
    examples = {
        "examples/mycode.anthropic.yaml": "${ANTHROPIC_API_KEY}",
        "examples/mycode.openai-responses.yaml": "${OPENAI_API_KEY}",
        "examples/mycode.openai-chat.yaml": "${OPENAI_API_KEY}",
    }

    for path, env_ref in examples.items():
        text = Path(path).read_text(encoding="utf-8")
        assert env_ref in text
        assert "sk-" not in text


def test_primary_example_configs_load_with_compact_settings(tmp_path):
    examples = {
        "examples/mycode.anthropic.yaml": {
            "environment": {"ANTHROPIC_API_KEY": "sk-anthropic-test"},
        },
        "examples/mycode.openai-responses.yaml": {
            "environment": {"OPENAI_API_KEY": "sk-openai-test"},
        },
        "examples/mycode.openai-chat.yaml": {
            "environment": {"OPENAI_API_KEY": "sk-openai-test"},
            "overrides": {
                "model": "gpt-test",
                "base_url": "https://api.openai.com/v1",
            },
        },
    }

    configs = {}
    for path, settings in examples.items():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        data.update(settings.get("overrides", {}))
        config_path = tmp_path / Path(path).name
        config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        configs[path] = load_config(
            config_path,
            cwd=tmp_path,
            home=tmp_path,
            environ=settings["environment"],
        )

    assert [config.protocol for config in configs.values()] == [
        "anthropic",
        "openai_responses",
        "openai_chat",
    ]
    assert configs["examples/mycode.anthropic.yaml"].compact.context_window_tokens == 200_000
    assert configs["examples/mycode.openai-responses.yaml"].compact.context_window_tokens == 128_000
    assert configs["examples/mycode.openai-chat.yaml"].compact.context_window_tokens == 128_000
    assert configs["examples/mycode.openai-chat.yaml"].usage.request_stream_usage is True


def test_readme_primary_config_examples_load(tmp_path):
    readme = Path("README.md").read_text(encoding="utf-8")
    yaml_blocks = re.findall(r"```yaml\n(.*?)\n```", readme, re.DOTALL)
    config_snippets = [block for block in yaml_blocks if block.startswith("protocol:")]

    assert len(config_snippets) == 4

    configs = []
    for index, snippet in enumerate(config_snippets):
        config_path = tmp_path / f"readme-config-{index}.yaml"
        config_path.write_text(snippet, encoding="utf-8")
        configs.append(
            load_config(
                config_path,
                cwd=tmp_path,
                home=tmp_path,
                environ={
                    "OPENAI_API_KEY": "sk-openai-test",
                    "ANTHROPIC_API_KEY": "sk-anthropic-test",
                },
            )
        )

    assert [config.protocol for config in configs] == [
        "openai_responses",
        "openai_responses",
        "openai_chat",
        "anthropic",
    ]


def test_readme_and_repository_example_document_stage_05_permission_boundaries():
    readme = Path("README.md").read_text(encoding="utf-8")
    required = [
        "Stage 05",
        "strict",
        "default",
        "permissive",
        "会话规则",
        "本地项目授权",
        "仓库项目策略",
        "用户全局",
        "DENY",
        "ASK",
        "FORBIDDEN",
        "HITL",
        "路径沙箱",
        "操作系统级进程沙箱",
        "/permission",
        "中文",
    ]
    assert all(value in readme for value in required)

    example_path = Path("examples/mycode.permissions.yaml")
    data = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "mode" not in data
    assert {rule["effect"] for rule in data["rules"]} <= {"deny", "ask"}
    assert all(rule["effect"] != "allow" for rule in data["rules"])


def test_mcp_example_is_safe_and_loads_both_supported_transports():
    example_path = Path("examples/mycode.mcp.yaml")
    text = example_path.read_text(encoding="utf-8")

    assert "${MCP_STDIO_TOKEN}" in text
    assert "${MCP_HTTP_TOKEN}" in text
    assert "sk-" not in text

    config, diagnostics = load_mcp_config(
        example_path,
        environ={
            "MCP_STDIO_TOKEN": "stdio-test-secret",
            "MCP_HTTP_TOKEN": "http-test-secret",
        },
    )

    assert diagnostics == ()
    assert [server.transport for server in config.servers] == [
        MCPTransportKind.STDIO,
        MCPTransportKind.STREAMABLE_HTTP,
    ]
    assert all(server.timeout_seconds > 0 for server in config.servers)
    assert config.servers[0].read_tools
    assert "stdio-test-secret" not in text
    assert "http-test-secret" not in text


def test_readme_documents_stage_06_mcp_behavior_and_boundaries():
    readme = Path("README.md").read_text(encoding="utf-8")
    required = [
        "Stage 06",
        "--mcp-config",
        "mycode.mcp.yaml",
        "~/.mycode/mcp.yaml",
        "stdio",
        "Streamable HTTP",
        "server_name__remote_name",
        "默认按写工具",
        "read_tools",
        "名称 + 描述",
        "tool_search",
        "下一轮",
        "连接复用",
        "故障隔离",
        "ping",
        "resources",
        "prompts",
        "sampling",
        "elicitation",
        "OAuth",
        "HTTP+SSE",
        "热重载",
        "持久化缓存",
    ]

    assert all(value in readme for value in required)


def test_readme_documents_stage_07_context_management():
    readme = Path("README.md").read_text(encoding="utf-8")
    required = [
        "Stage 07",
        "compact.context_window_tokens",
        "context_window_tokens",
        "8K",
        "12K",
        "2K",
        "13K",
        "3K",
        "10K",
        "/compact",
        "read_compact_artifact",
        "~/.mycode/projects",
        "24 小时",
        "熔断",
        "应急压缩",
        "分段读取",
        "归档",
        "上下文缓存",
    ]

    assert all(value in readme for value in required)
