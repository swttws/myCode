from pathlib import Path


def test_readme_documents_supported_protocols_and_stage_02_tool_scope():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Stage 02" in readme
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
    assert "单轮工具调用" in readme
    assert "Agent Loop" in readme
    assert "多工具连环调用" in readme
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
