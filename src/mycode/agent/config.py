from __future__ import annotations

from dataclasses import dataclass, field

from mycode.prompt import PromptConfig


@dataclass(frozen=True)
class AgentConfig:
    max_rounds: int = 8
    model_timeout_seconds: float | None = None
    run_timeout_seconds: float | None = None
    prompt: PromptConfig = field(default_factory=PromptConfig)
