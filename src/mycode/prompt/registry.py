from __future__ import annotations

from collections.abc import Sequence

from mycode.prompt.models import PromptModule


class PromptConfigurationError(ValueError):
    """提示词模块配置无效。"""


class PromptBuildError(RuntimeError):
    """提示词构建无法安全完成。"""


class PromptRegistry:
    def __init__(self, modules: Sequence[PromptModule] | None = None) -> None:
        self._modules: dict[str, PromptModule] = {}
        self._enabled: set[str] = set()
        for module in modules or ():
            self.register(module)

    def register(self, module: PromptModule, *, enabled: bool = True) -> None:
        module_id = module.definition.id
        if not module_id:
            raise PromptConfigurationError("module id must not be empty")
        if module_id in self._modules:
            raise PromptConfigurationError(f"duplicate prompt module: {module_id}")
        self._modules[module_id] = module
        if enabled:
            self._enabled.add(module_id)

    def enable(self, module_id: str) -> None:
        self._get(module_id)
        self._enabled.add(module_id)

    def disable(self, module_id: str) -> None:
        module = self._get(module_id)
        # 安全边界必须始终参与稳定指令，不能由运行时配置移除。
        if module.definition.protected:
            raise PromptConfigurationError(f"protected prompt module cannot be disabled: {module_id}")
        self._enabled.discard(module_id)

    def override(self, module: PromptModule) -> None:
        module_id = module.definition.id
        existing = self._get(module_id)
        # 不允许用自定义文本替换受保护模块，以免削弱安全约束。
        if existing.definition.protected:
            raise PromptConfigurationError(f"protected prompt module cannot be overridden: {module_id}")
        self._modules[module_id] = module

    def enabled_modules(self) -> tuple[PromptModule, ...]:
        return tuple(
            sorted(
                (self._modules[module_id] for module_id in self._enabled),
                key=lambda module: (module.definition.priority, module.definition.id),
            )
        )

    def _get(self, module_id: str) -> PromptModule:
        module = self._modules.get(module_id)
        if module is None:
            raise PromptConfigurationError(f"unknown prompt module: {module_id}")
        return module
