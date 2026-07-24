from __future__ import annotations

from mycode.memory.models import MemoryScope
from mycode.memory.notes import MemoryNoteStore
from mycode.tool.base import ToolArguments, ToolDefinition, ToolKind, ToolResult


class ReadMemoryNoteTool:
    def __init__(self, notes: MemoryNoteStore) -> None:
        self._notes = notes

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_memory_note",
            description="按作用域和 note_id 读取长期记忆正文。仅用于读取 myCode 长期记忆，不读取普通文件路径。",
            parameters={
                "type": "object",
                "description": "读取长期记忆正文所需参数。",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["user", "project"],
                        "description": "记忆作用域：user 或 project。",
                    },
                    "note_id": {
                        "type": "string",
                        "description": "记忆索引中提供的 note id。",
                    },
                },
                "required": ["scope", "note_id"],
            },
            kind=ToolKind.READ,
        )

    def execute(self, arguments: ToolArguments) -> ToolResult:
        tool_name = self.definition.name
        try:
            scope_value = _required_str(arguments, "scope")
            note_id = _required_str(arguments, "note_id")
        except Exception as exc:
            return ToolResult(ok=False, tool_name=tool_name, content={}, error=str(exc))

        try:
            scope = MemoryScope(scope_value)
        except ValueError:
            return ToolResult(
                ok=False,
                tool_name=tool_name,
                content={"scope": scope_value, "note_id": note_id},
                error="scope must be 'user' or 'project'",
            )

        note = self._notes.read_note(scope, note_id)
        if note is None:
            return ToolResult(
                ok=False,
                tool_name=tool_name,
                content={"scope": scope.value, "note_id": note_id},
                error="memory note not found",
            )

        return ToolResult(
            ok=True,
            tool_name=tool_name,
            content={
                "scope": note.scope.value,
                "note_id": note.note_id,
                "kind": note.kind.value,
                "title": note.frontmatter.get("title", note.note_id),
                "updated_at": note.updated_at,
                "body": note.body,
            },
        )


def _required_str(arguments: ToolArguments, name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value
