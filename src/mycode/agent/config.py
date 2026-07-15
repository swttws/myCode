from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    max_rounds: int = 8
    model_timeout_seconds: float | None = None
    run_timeout_seconds: float | None = None
    minimal_system_prompt: str = (
        "You are myCode, a terminal coding assistant. "
        "Use tools when needed. In plan-only mode, produce a plan for user approval "
        "and do not assume write tools are allowed unless approved."
    )
