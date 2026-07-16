import pytest

from mycode.prompt.models import PromptConfig, PromptModuleDefinition
from mycode.prompt.modules import create_builtin_modules
from mycode.prompt.registry import PromptConfigurationError, PromptRegistry


class FakeModule:
    def __init__(self, module_id: str, priority: int, *, protected: bool = False) -> None:
        self._definition = PromptModuleDefinition(module_id, priority, protected)

    @property
    def definition(self) -> PromptModuleDefinition:
        return self._definition

    def render(self, context) -> str:
        return self.definition.id


def test_prompt_config_defaults_and_rejects_non_positive_values():
    assert PromptConfig() == PromptConfig(
        full_reminder_interval_rounds=4,
        environment_value_limit=512,
        git_timeout_seconds=1.0,
    )

    with pytest.raises(ValueError):
        PromptConfig(full_reminder_interval_rounds=0)
    with pytest.raises(ValueError):
        PromptConfig(environment_value_limit=0)
    with pytest.raises(ValueError):
        PromptConfig(git_timeout_seconds=0)


def test_registry_sorts_enabled_modules_by_priority_then_id():
    registry = PromptRegistry(
        [
            FakeModule("zeta", 200),
            FakeModule("alpha", 200),
            FakeModule("first", 100),
        ]
    )

    assert [module.definition.id for module in registry.enabled_modules()] == ["first", "alpha", "zeta"]


def test_registry_rejects_duplicates_and_unknown_module_state_changes():
    registry = PromptRegistry([FakeModule("one", 100)])

    with pytest.raises(PromptConfigurationError, match="duplicate"):
        registry.register(FakeModule("one", 200))
    with pytest.raises(PromptConfigurationError, match="unknown"):
        registry.enable("missing")
    with pytest.raises(PromptConfigurationError, match="unknown"):
        registry.disable("missing")
    with pytest.raises(PromptConfigurationError, match="unknown"):
        registry.override(FakeModule("missing", 100))


def test_registry_can_disable_and_override_regular_module_but_not_protected_module():
    registry = PromptRegistry([FakeModule("regular", 100), FakeModule("safety", 200, protected=True)])

    registry.disable("regular")
    assert [module.definition.id for module in registry.enabled_modules()] == ["safety"]
    registry.override(FakeModule("regular", 50))
    registry.enable("regular")
    assert [module.definition.id for module in registry.enabled_modules()] == ["regular", "safety"]

    with pytest.raises(PromptConfigurationError, match="protected"):
        registry.disable("safety")
    with pytest.raises(PromptConfigurationError, match="protected"):
        registry.override(FakeModule("safety", 50, protected=True))


def test_builtin_modules_have_expected_stable_order_and_protection():
    modules = create_builtin_modules()

    assert [module.definition.id for module in modules] == [
        "safety-boundaries",
        "identity",
        "behavior",
        "tool-usage",
        "coding-standards",
        "output-style",
    ]
    assert [module.definition.priority for module in modules] == [100, 200, 300, 400, 500, 600]
    assert modules[0].definition.protected is True
    assert all(not module.definition.protected for module in modules[1:])
