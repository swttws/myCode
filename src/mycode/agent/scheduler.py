from __future__ import annotations

from dataclasses import dataclass

from mycode.tool import ToolCall, ToolKind, ToolRegistry


@dataclass(frozen=True)
class ToolBatch:
    kind: ToolKind
    calls: tuple[ToolCall, ...]


class ToolScheduleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def build_tool_batches(calls: list[ToolCall], registry: ToolRegistry) -> list[ToolBatch]:
    # 保持模型给出的顺序语义：连续读合并成批，写工具作为顺序边界单独执行。
    batches: list[ToolBatch] = []
    pending_reads: list[ToolCall] = []

    for call in calls:
        tool = registry.get(call.name)
        if tool is None:
            raise ToolScheduleError("unknown_tool", f"unknown tool: {call.name}")

        kind = tool.definition.kind
        if kind not in (ToolKind.READ, ToolKind.WRITE):
            raise ToolScheduleError("invalid_tool_kind", f"invalid tool kind: {kind}")

        if kind == ToolKind.READ:
            # 读工具先暂存，直到遇到写工具或输入结束时再形成一个并发批次。
            pending_reads.append(call)
            continue

        if pending_reads:
            batches.append(ToolBatch(kind=ToolKind.READ, calls=tuple(pending_reads)))
            pending_reads = []
        # 写工具可能产生副作用，不能和其他工具并发。
        batches.append(ToolBatch(kind=ToolKind.WRITE, calls=(call,)))

    if pending_reads:
        batches.append(ToolBatch(kind=ToolKind.READ, calls=tuple(pending_reads)))

    return batches
