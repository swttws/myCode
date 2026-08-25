from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from mycode.subagent.models import (
    AgentIsolationMode,
    AgentModelTier,
    AgentPermissionMode,
    AgentRoleDefinition,
    AgentRoleDiagnostic,
    AgentRoleMetadata,
    AgentRoleSource,
)
from mycode.team.tooling.tool_names import LEGACY_TEAM_TOOL_NAMES


MAX_ROLE_FILE_BYTES = 128 * 1024
MAX_FRONTMATTER_BYTES = 16 * 1024
ROLE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ROLE_FIELDS = {
    "name",
    "description",
    "allowed_tools",
    "denied_tools",
    "model",
    "max_rounds",
    "permission_mode",
    "isolation",
}
REQUIRED_ROLE_FIELDS = ROLE_FIELDS - {"isolation"}
SOURCE_ORDER = {
    AgentRoleSource.PLUGIN: 0,
    AgentRoleSource.BUILTIN: 1,
    AgentRoleSource.USER: 2,
    AgentRoleSource.PROJECT: 3,
}


@dataclass(frozen=True)
class AgentRoleCandidate:
    role_name: str
    source: AgentRoleSource
    path: Path
    definition: AgentRoleDefinition | None
    diagnostics: tuple[AgentRoleDiagnostic, ...] = ()


class AgentRoleLoader:
    def __init__(
        self,
        *,
        project_root: str | Path,
        home: str | Path,
        builtin_dir: str | Path,
        known_tool_names: Iterable[str],
        plugin_dirs: tuple[str | Path, ...] = (),
    ) -> None:
        self._project_root = Path(project_root)
        self._home = Path(home)
        self._builtin_dir = Path(builtin_dir)
        self._plugin_dirs = tuple(Path(path) for path in plugin_dirs)
        self._known_tool_names = frozenset(known_tool_names)

    def load(self) -> tuple[AgentRoleCandidate, ...]:
        candidates: list[AgentRoleCandidate] = []
        for source, directory in self._source_directories():
            candidates.extend(self._load_directory(source, directory))
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    SOURCE_ORDER[candidate.source],
                    str(candidate.path),
                    candidate.role_name,
                ),
            )
        )

    def _source_directories(self) -> tuple[tuple[AgentRoleSource, Path], ...]:
        plugin_sources = tuple((AgentRoleSource.PLUGIN, path) for path in self._plugin_dirs)
        return (
            *plugin_sources,
            (AgentRoleSource.BUILTIN, self._builtin_dir),
            (AgentRoleSource.USER, self._home / ".mycode" / "agents"),
            (AgentRoleSource.PROJECT, self._project_root / ".mycode" / "agents"),
        )

    def _load_directory(
        self,
        source: AgentRoleSource,
        directory: Path,
    ) -> tuple[AgentRoleCandidate, ...]:
        if not directory.exists():
            return ()
        paths = sorted(path for path in directory.iterdir() if path.suffix == ".md")
        return tuple(self._load_file(source, path) for path in paths if path.is_file())

    def _load_file(self, source: AgentRoleSource, path: Path) -> AgentRoleCandidate:
        role_name = path.stem
        try:
            if path.stat().st_size > MAX_ROLE_FILE_BYTES:
                return self._invalid(role_name, source, path, "role_file_too_large")
            text = path.read_text(encoding="utf-8")
        except OSError:
            return self._invalid(role_name, source, path, "role_file_unreadable")
        except UnicodeDecodeError:
            return self._invalid(role_name, source, path, "role_file_not_utf8")

        parsed = self._parse_markdown(text)
        if isinstance(parsed, str):
            return self._invalid(role_name, source, path, parsed)
        frontmatter, body = parsed
        if len(frontmatter.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
            return self._invalid(role_name, source, path, "frontmatter_too_large")

        metadata = self._parse_frontmatter(frontmatter, role_name, source, path)
        if isinstance(metadata, AgentRoleDiagnostic):
            return AgentRoleCandidate(
                role_name=role_name,
                source=source,
                path=path,
                definition=None,
                diagnostics=(metadata,),
            )
        instruction = body.strip()
        if not instruction:
            return self._invalid(role_name, source, path, "empty_body")

        revision = hashlib.sha256(text.encode("utf-8")).hexdigest()
        definition = AgentRoleDefinition(
            metadata=metadata,
            instruction=instruction,
            source=source,
            entry_path=path,
            revision=revision,
        )
        return AgentRoleCandidate(
            role_name=role_name,
            source=source,
            path=path,
            definition=definition,
        )

    def _parse_markdown(self, text: str) -> tuple[str, str] | str:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return "missing_frontmatter"
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                frontmatter = "\n".join(lines[1:index])
                body = "\n".join(lines[index + 1 :])
                return frontmatter, body
        return "missing_frontmatter"

    def _parse_frontmatter(
        self,
        frontmatter: str,
        role_name: str,
        source: AgentRoleSource,
        path: Path,
    ) -> AgentRoleMetadata | AgentRoleDiagnostic:
        try:
            raw = yaml.safe_load(frontmatter)
        except yaml.YAMLError:
            return self._diagnostic(role_name, source, path, "invalid_yaml")
        if not isinstance(raw, dict):
            return self._diagnostic(role_name, source, path, "invalid_frontmatter")

        unknown_fields = set(raw) - ROLE_FIELDS
        if unknown_fields:
            return self._diagnostic(role_name, source, path, "unknown_field")
        missing = [field for field in REQUIRED_ROLE_FIELDS if field not in raw]
        if missing:
            return self._diagnostic(role_name, source, path, "missing_field")

        name = raw["name"]
        if type(name) is not str or not ROLE_NAME_PATTERN.fullmatch(name):
            return self._diagnostic(role_name, source, path, "invalid_role_name")
        if name != role_name:
            return self._diagnostic(role_name, source, path, "role_name_mismatch")
        description = raw["description"]
        if type(description) is not str or not description:
            return self._diagnostic(role_name, source, path, "invalid_description")

        allowed = self._parse_tool_list(raw["allowed_tools"], "allowed", role_name, source, path)
        if isinstance(allowed, AgentRoleDiagnostic):
            return allowed
        denied = self._parse_tool_list(raw["denied_tools"], "denied", role_name, source, path)
        if isinstance(denied, AgentRoleDiagnostic):
            return denied

        model = self._parse_model(raw["model"], role_name, source, path)
        if isinstance(model, AgentRoleDiagnostic):
            return model
        permission_mode = self._parse_permission(raw["permission_mode"], role_name, source, path)
        if isinstance(permission_mode, AgentRoleDiagnostic):
            return permission_mode
        isolation = self._parse_isolation(raw.get("isolation", "shared"), role_name, source, path)
        if isinstance(isolation, AgentRoleDiagnostic):
            return isolation
        max_rounds = raw["max_rounds"]
        if type(max_rounds) is not int or max_rounds <= 0:
            return self._diagnostic(role_name, source, path, "invalid_max_rounds")

        return AgentRoleMetadata(
            name=name,
            description=description,
            allowed_tools=allowed,
            denied_tools=denied,
            model=model,
            max_rounds=max_rounds,
            permission_mode=permission_mode,
            isolation=isolation,
        )

    def _parse_tool_list(
        self,
        raw: object,
        field_kind: str,
        role_name: str,
        source: AgentRoleSource,
        path: Path,
    ) -> tuple[str, ...] | AgentRoleDiagnostic:
        if not isinstance(raw, list):
            return self._diagnostic(role_name, source, path, f"invalid_{field_kind}_tools")
        values: list[str] = []
        seen: set[str] = set()
        for value in raw:
            if type(value) is not str or not value:
                return self._diagnostic(role_name, source, path, f"invalid_{field_kind}_tools")
            if value in seen:
                return self._diagnostic(role_name, source, path, "duplicate_tool")
            seen.add(value)
            values.append(value)
        if field_kind == "allowed" and "*" in seen and len(seen) > 1:
            return self._diagnostic(role_name, source, path, "invalid_allowed_tools")
        if field_kind == "denied" and "*" in seen:
            return self._diagnostic(role_name, source, path, "invalid_denied_tools")
        unknown = [value for value in values if value != "*" and value not in self._known_tool_names]
        if unknown:
            legacy = sorted(set(unknown) & LEGACY_TEAM_TOOL_NAMES)
            if legacy:
                return self._diagnostic(role_name, source, path, "legacy_team_tool")
            return self._diagnostic(role_name, source, path, "unknown_tool")
        return tuple(values)

    def _parse_model(
        self,
        raw: object,
        role_name: str,
        source: AgentRoleSource,
        path: Path,
    ) -> AgentModelTier | AgentRoleDiagnostic:
        if type(raw) is not str:
            return self._diagnostic(role_name, source, path, "invalid_model")
        try:
            return AgentModelTier(raw)
        except ValueError:
            return self._diagnostic(role_name, source, path, "invalid_model")

    def _parse_permission(
        self,
        raw: object,
        role_name: str,
        source: AgentRoleSource,
        path: Path,
    ) -> AgentPermissionMode | AgentRoleDiagnostic:
        if type(raw) is not str:
            return self._diagnostic(role_name, source, path, "invalid_permission_mode")
        try:
            return AgentPermissionMode(raw)
        except ValueError:
            return self._diagnostic(role_name, source, path, "invalid_permission_mode")

    def _parse_isolation(
        self,
        raw: object,
        role_name: str,
        source: AgentRoleSource,
        path: Path,
    ) -> AgentIsolationMode | AgentRoleDiagnostic:
        if type(raw) is not str:
            return self._diagnostic(role_name, source, path, "invalid_isolation")
        try:
            return AgentIsolationMode(raw)
        except ValueError:
            return self._diagnostic(role_name, source, path, "invalid_isolation")

    def _invalid(
        self,
        role_name: str,
        source: AgentRoleSource,
        path: Path,
        code: str,
    ) -> AgentRoleCandidate:
        return AgentRoleCandidate(
            role_name=role_name,
            source=source,
            path=path,
            definition=None,
            diagnostics=(self._diagnostic(role_name, source, path, code),),
        )

    def _diagnostic(
        self,
        role_name: str,
        source: AgentRoleSource,
        path: Path,
        code: str,
    ) -> AgentRoleDiagnostic:
        message = f"角色 {role_name} 无效：{code}"
        if code == "legacy_team_tool":
            message = f"角色 {role_name} 引用了已移除的旧团队工具，请改用新的 team_* 工具名"
        return AgentRoleDiagnostic(
            code=code,
            source=source,
            path=str(path),
            message=message,
            role_name=role_name,
        )
