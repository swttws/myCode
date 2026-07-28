"""Hook lifecycle automation support."""

from mycode.hook.config import load_hook_config, load_hook_file
from mycode.hook.models import (
    HookAction,
    HookActionResult,
    HookActionType,
    HookCondition,
    HookConfig,
    HookConfigError,
    HookContext,
    HookError,
    HookEvent,
    HookExecutionError,
    HookPredicate,
    HookPromptInjection,
    HookRule,
    HookTriggerResult,
    MatchKind,
    ValueMatcher,
)

__all__ = [
    "HookAction",
    "HookActionResult",
    "HookActionType",
    "HookCondition",
    "HookConfig",
    "HookConfigError",
    "HookContext",
    "HookError",
    "HookEvent",
    "HookExecutionError",
    "HookPredicate",
    "HookPromptInjection",
    "HookRule",
    "HookTriggerResult",
    "MatchKind",
    "ValueMatcher",
    "load_hook_config",
    "load_hook_file",
]
