"""提示词构建边界的公共入口。"""

from pathlib import Path

from mycode.prompt.builder import PromptBuilder
from mycode.prompt.environment import DefaultEnvironmentCollector
from mycode.prompt.models import PromptConfig
from mycode.prompt.modules import create_builtin_modules
from mycode.prompt.registry import PromptBuildError, PromptConfigurationError, PromptRegistry
from mycode.prompt.reminder import ReminderPolicy


def create_default_prompt_builder(
    workspace_root: str | Path,
    config: PromptConfig | None = None,
) -> PromptBuilder:
    resolved_config = config or PromptConfig()
    return PromptBuilder(
        registry=PromptRegistry(create_builtin_modules()),
        environment_collector=DefaultEnvironmentCollector(workspace_root, resolved_config),
        reminder_policy=ReminderPolicy(resolved_config.full_reminder_interval_rounds),
        config=resolved_config,
    )


__all__ = [
    "PromptBuildError",
    "PromptBuilder",
    "PromptConfig",
    "PromptConfigurationError",
    "PromptRegistry",
    "create_default_prompt_builder",
]
