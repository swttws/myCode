from __future__ import annotations

from pathlib import Path
from typing import Any

from mycode.llm import ChatMessage, StreamEvent, StreamEventType
from mycode.tool import ToolDefinition, ToolKind, ToolRegistry, ToolResult


def write_skill(
    root: Path,
    name: str,
    *,
    body: str = "执行 {{arguments}}。",
    description: str = "测试 Skill。",
    allowed_tools: tuple[str, ...] = ("read_file",),
    mode: str = "shared",
    context: dict[str, Any] | None = None,
    model: str | None = None,
    frontmatter: dict[str, Any] | None = None,
    resources: dict[str, str] | None = None,
) -> Path:
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, Any] = {
        "name": name,
        "description": description,
        "allowed_tools": list(allowed_tools),
        "mode": mode,
    }
    if context is not None:
        metadata["context"] = context
    if model is not None:
        metadata["model"] = model
    if frontmatter is not None:
        metadata = frontmatter

    lines = ["---"]
    for key, value in metadata.items():
        if isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                lines.extend(f"  - {item}" for item in value)
            else:
                lines.append(f"{key}: []")
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for child_key, child_value in value.items():
                lines.append(f"  {child_key}: {child_value}")
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", "", body])
    (package / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")

    for relative_path, text in (resources or {}).items():
        path = package / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return package


class FakeLLM:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or ["完成"]
        self.requests: list[dict[str, Any]] = []

    async def stream_chat(self, messages: list[ChatMessage], *, tools=None):
        self.requests.append({"messages": tuple(messages), "tools": tuple(tools or ())})
        response = self.responses.pop(0) if self.responses else "完成"
        yield StreamEvent(StreamEventType.TEXT_DELTA, content=response)
        yield StreamEvent(StreamEventType.DONE)


class FakeTool:
    def __init__(self, name: str = "read_file", *, kind: ToolKind = ToolKind.READ) -> None:
        self._definition = ToolDefinition(
            name=name,
            description=f"{name} fake tool.",
            parameters={"type": "object", "properties": {}, "required": []},
            kind=kind,
        )
        self.calls: list[dict[str, Any]] = []

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def execute(self, arguments):
        self.calls.append(dict(arguments or {}))
        return ToolResult(ok=True, tool_name=self.definition.name, content={"ok": True})


def fixed_tool_registry(*names: str) -> ToolRegistry:
    return ToolRegistry([FakeTool(name) for name in names or ("read_file", "search_code", "run_command")])
