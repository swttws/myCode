from __future__ import annotations

from mycode.subagent.loader import AgentRoleCandidate, AgentRoleLoader, SOURCE_ORDER
from mycode.subagent.models import (
    AgentCatalogSnapshot,
    AgentRoleDefinition,
    AgentRoleDiagnostic,
)


class AgentCatalog:
    def __init__(self, loader: AgentRoleLoader) -> None:
        self._loader = loader
        self._snapshot: AgentCatalogSnapshot | None = None
        self._definitions_by_name: dict[str, AgentRoleDefinition] = {}

    def initialize(self) -> AgentCatalogSnapshot:
        if self._snapshot is not None:
            raise RuntimeError("AgentCatalog is already initialized.")

        candidates = self._loader.load()
        diagnostics = self._collect_diagnostics(candidates)
        definitions = self._select_definitions(candidates)
        self._definitions_by_name = {
            definition.metadata.name: definition for definition in definitions
        }
        self._snapshot = AgentCatalogSnapshot(
            definitions=definitions,
            diagnostics=diagnostics,
            generation=1,
        )
        return self._snapshot

    def snapshot(self) -> AgentCatalogSnapshot:
        if self._snapshot is None:
            raise RuntimeError("AgentCatalog is not initialized.")
        return self._snapshot

    def get(self, name: str) -> AgentRoleDefinition:
        if self._snapshot is None:
            raise RuntimeError("AgentCatalog is not initialized.")
        try:
            return self._definitions_by_name[name]
        except KeyError as exc:
            raise KeyError(f"Agent role not found: {name}") from exc

    def _select_definitions(
        self,
        candidates: tuple[AgentRoleCandidate, ...],
    ) -> tuple[AgentRoleDefinition, ...]:
        by_name: dict[str, list[AgentRoleCandidate]] = {}
        for candidate in candidates:
            by_name.setdefault(candidate.role_name, []).append(candidate)

        selected: list[AgentRoleDefinition] = []
        for role_name in sorted(by_name):
            ordered = sorted(
                by_name[role_name],
                key=lambda candidate: (
                    -SOURCE_ORDER[candidate.source],
                    str(candidate.path),
                ),
            )
            for candidate in ordered:
                if candidate.definition is not None:
                    selected.append(candidate.definition)
                    break
        return tuple(sorted(selected, key=lambda definition: definition.metadata.name))

    def _collect_diagnostics(
        self,
        candidates: tuple[AgentRoleCandidate, ...],
    ) -> tuple[AgentRoleDiagnostic, ...]:
        diagnostics = [
            diagnostic
            for candidate in candidates
            for diagnostic in candidate.diagnostics
        ]
        return tuple(
            sorted(
                diagnostics,
                key=lambda diagnostic: (
                    -SOURCE_ORDER[diagnostic.source],
                    diagnostic.path,
                    diagnostic.code,
                ),
            )
        )
