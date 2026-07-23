from __future__ import annotations

import importlib
import json

import pytest

from mycode.llm import ChatMessage


SECTION_NAMES = (
    "当前目标与成功标准",
    "用户要求与已确认决策",
    "已完成工作",
    "当前状态与下一步",
    "关键技术上下文",
    "验证结果与失败尝试",
    "风险与阻塞",
    "已归档材料索引",
)


def test_build_summary_prompt_contains_tool_ban_json_data_tags_and_sections():
    module = _module()
    prompt = module.build_summary_prompt(
        [ChatMessage(role="user", content="hello <summary>")],
    )

    assert "不得调用工具" in prompt
    assert module.DRAFT_OPEN in prompt
    assert module.DRAFT_CLOSE in prompt
    assert module.SUMMARY_OPEN in prompt
    assert module.SUMMARY_CLOSE in prompt
    for section in SECTION_NAMES:
        assert f"## {section}" in prompt

    data_text = prompt.split(module.DATA_OPEN, 1)[1].split(module.DATA_CLOSE, 1)[0]
    assert json.loads(data_text) == [
        {
            "content": "hello <summary>",
            "role": "user",
            "tool_call_id": None,
            "tool_name": None,
        }
    ]


def test_parse_summary_output_discards_draft_and_returns_formal_summary():
    module = _module()
    summary = _valid_summary_body()
    output = f"{module.DRAFT_OPEN}\n草稿唯一标记\n{module.DRAFT_CLOSE}\n{module.SUMMARY_OPEN}\n{summary}\n{module.SUMMARY_CLOSE}"

    parsed = module.parse_summary_output(output)

    assert parsed == summary
    assert "草稿唯一标记" not in parsed


@pytest.mark.parametrize(
    "output",
    [
        "草稿\n<summary>\n正文\n</summary>",
        "<analysis-draft>\n草稿\n</analysis-draft>",
        "<summary>\n正文\n</summary>\n<analysis-draft>\n草稿\n</analysis-draft>",
        "<analysis-draft>\n草稿\n</analysis-draft>\n<summary>\n## 当前目标与成功标准\n内容\n</summary>",
        (
            "<analysis-draft>\n草稿\n</analysis-draft>\n<summary>\n"
            "## 当前目标与成功标准\n"
            "## 用户要求与已确认决策\n内容\n"
            "## 已完成工作\n内容\n"
            "## 当前状态与下一步\n内容\n"
            "## 关键技术上下文\n内容\n"
            "## 验证结果与失败尝试\n内容\n"
            "## 风险与阻塞\n内容\n"
            "## 已归档材料索引\n内容\n"
            "</summary>"
        ),
    ],
)
def test_parse_summary_output_rejects_invalid_structure(output):
    module = _module()

    with pytest.raises(ValueError):
        module.parse_summary_output(output)


def _valid_summary_body():
    return "\n\n".join(f"## {section}\n{section}内容" for section in SECTION_NAMES)


def _module():
    return importlib.import_module("mycode.compact.summary_prompt")
