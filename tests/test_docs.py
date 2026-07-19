from pathlib import Path

import yaml


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
