from pathlib import Path


def test_readme_documents_supported_protocols_and_scope():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "anthropic" in readme
    assert "openai_responses" in readme
    assert "openai_chat" in readme
    assert "thinking" in readme
    assert "tool use" in readme
    assert "文件操作" in readme
    assert "代码编辑" in readme
    assert "shell" in readme
    assert "持久化会话" in readme


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
