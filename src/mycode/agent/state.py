from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentMode:
    plan_only: bool = False

    def reset(self) -> None:
        self.plan_only = False
