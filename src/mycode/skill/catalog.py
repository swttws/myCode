from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from mycode.skill.loader import SkillLoader
from mycode.skill.models import (
    SOURCE_PRIORITY,
    SkillCatalogSnapshot,
    SkillDefinition,
    SkillDiagnostic,
    SkillParseError,
    SkillSource,
    SkillStartupError,
)
from mycode.team.tooling.tool_names import LEGACY_TEAM_TOOL_NAMES


class SkillCatalog:
    def __init__(
        self,
        *,
        loader: SkillLoader,
        tool_names: Callable[[], frozenset[str]],
        reserved_slash_names: frozenset[str],
    ) -> None:
        self._loader = loader
        self._tool_names = tool_names
        self._reserved_slash_names = frozenset(reserved_slash_names)
        self._snapshot = SkillCatalogSnapshot(definitions=(), diagnostics=(), generation=0)
        self._last_valid: dict[str, SkillDefinition] = {}

    def initialize(self) -> SkillCatalogSnapshot:
        definitions, diagnostics = self._load_effective_definitions()
        semantic_diagnostics = self._semantic_diagnostics(definitions)
        if semantic_diagnostics:
            details = "; ".join(
                f"{diagnostic.skill_name}: {diagnostic.message}" for diagnostic in semantic_diagnostics
            )
            raise SkillStartupError(details)
        snapshot = SkillCatalogSnapshot(
            definitions=definitions,
            diagnostics=diagnostics,
            generation=1,
        )
        self._snapshot = snapshot
        self._last_valid = {definition.metadata.name: definition for definition in definitions}
        return snapshot

    def refresh(self) -> SkillCatalogSnapshot:
        definitions, diagnostics = self._load_effective_definitions()
        valid_definitions: dict[str, SkillDefinition] = {}
        for definition in definitions:
            semantic_diagnostics = self._semantic_diagnostics((definition,))
            if semantic_diagnostics:
                diagnostics.extend(semantic_diagnostics)
                previous = self._last_valid.get(definition.metadata.name)
                # 热更新语义错误只拒绝受影响 Skill，新版本修好前继续保留最后有效版本。
                if previous is not None:
                    valid_definitions[previous.metadata.name] = previous
                continue
            valid_definitions[definition.metadata.name] = definition

        merged = tuple(sorted(valid_definitions.values(), key=lambda item: item.metadata.name))
        generation = self._snapshot.generation
        if _definition_signature(merged) != _definition_signature(self._snapshot.definitions):
            generation += 1
        snapshot = SkillCatalogSnapshot(
            definitions=merged,
            diagnostics=tuple(_dedupe_diagnostics(diagnostics)),
            generation=generation,
        )
        self._snapshot = snapshot
        self._last_valid = {definition.metadata.name: definition for definition in merged}
        return snapshot

    def snapshot(self) -> SkillCatalogSnapshot:
        return self._snapshot

    def get(self, name: str) -> SkillDefinition | None:
        for definition in self._snapshot.definitions:
            if definition.metadata.name == name:
                return definition
        return None

    def read_resource(self, name: str, relative_path: str) -> str:
        definition = self.get(name)
        if definition is None:
            raise KeyError(name)
        return self._loader.read_resource(definition, relative_path)

    def _load_effective_definitions(self) -> tuple[tuple[SkillDefinition, ...], list[SkillDiagnostic]]:
        scan = self._loader.scan()
        diagnostics = list(scan.diagnostics)
        by_name = defaultdict(list)
        for candidate in scan.candidates:
            by_name[candidate.package_root.name].append(candidate)

        definitions: list[SkillDefinition] = []
        for name in sorted(by_name):
            for candidate in sorted(
                by_name[name],
                key=lambda item: SOURCE_PRIORITY[item.source],
                reverse=True,
            ):
                try:
                    definitions.append(self._loader.load(candidate))
                    break
                except SkillParseError as exc:
                    diagnostics.append(
                        SkillDiagnostic(
                            code="parse_error",
                            source=candidate.source,
                            path=str(candidate.entry_path),
                            message=str(exc),
                            skill_name=name,
                        )
                    )
        return tuple(sorted(definitions, key=lambda item: item.metadata.name)), diagnostics

    def _semantic_diagnostics(self, definitions: tuple[SkillDefinition, ...]) -> tuple[SkillDiagnostic, ...]:
        available_tools = self._tool_names()
        diagnostics: list[SkillDiagnostic] = []
        for definition in definitions:
            unknown_tools = sorted(set(definition.metadata.allowed_tools) - available_tools)
            if unknown_tools:
                legacy = sorted(set(unknown_tools) & LEGACY_TEAM_TOOL_NAMES)
                message = (
                    f"已移除旧团队工具：{', '.join(legacy)}，请改用新的 team_* 工具名"
                    if legacy
                    else f"未知工具：{', '.join(unknown_tools)}"
                )
                diagnostics.append(
                    SkillDiagnostic(
                        code="unknown_tool",
                        source=definition.source,
                        path=str(definition.entry_path),
                        message=message,
                        skill_name=definition.metadata.name,
                    )
                )
            if definition.metadata.name in self._reserved_slash_names:
                diagnostics.append(
                    SkillDiagnostic(
                        code="reserved_slash_name",
                        source=definition.source,
                        path=str(definition.entry_path),
                        message=f"Skill 名称与固定斜杠命令冲突：{definition.metadata.name}",
                        skill_name=definition.metadata.name,
                    )
                )
        return tuple(diagnostics)


def _definition_signature(definitions: tuple[SkillDefinition, ...]) -> tuple[tuple[str, str, SkillSource], ...]:
    return tuple((definition.metadata.name, definition.revision, definition.source) for definition in definitions)


def _dedupe_diagnostics(diagnostics: list[SkillDiagnostic]) -> tuple[SkillDiagnostic, ...]:
    seen = set()
    deduped: list[SkillDiagnostic] = []
    for diagnostic in diagnostics:
        key = (diagnostic.code, diagnostic.source, diagnostic.path, diagnostic.message, diagnostic.skill_name)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(diagnostic)
    return tuple(deduped)
