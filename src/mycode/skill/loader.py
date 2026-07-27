from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from mycode.skill.models import (
    MAX_FRONTMATTER_BYTES,
    MAX_RESOURCE_BYTES,
    MAX_RESOURCE_COUNT,
    MAX_SKILL_FILE_BYTES,
    SkillCandidate,
    SkillContextPolicy,
    SkillContextStrategy,
    SkillDefinition,
    SkillDiagnostic,
    SkillMetadata,
    SkillMode,
    SkillParseError,
    SkillResourceError,
    SkillScanResult,
    SkillSource,
)

_ENTRY_NAME = "SKILL.md"
_METADATA_FIELDS = {"name", "description", "allowed_tools", "mode", "context", "model"}


class SkillLoader:
    def __init__(
        self,
        *,
        workspace_root: Path,
        home: Path,
        builtin_root: Path,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._home = Path(home)
        self._builtin_root = Path(builtin_root)

    def scan(self) -> SkillScanResult:
        candidates: list[SkillCandidate] = []
        diagnostics: list[SkillDiagnostic] = []
        for source, root in self._source_roots():
            if not root.exists():
                continue
            for package_root in sorted(root.iterdir(), key=lambda path: path.name):
                if not package_root.is_dir():
                    continue
                if package_root.is_symlink():
                    diagnostics.append(_diagnostic("symlink_package", source, package_root, "拒绝符号链接 Skill 目录。"))
                    continue
                entry_path = package_root / _ENTRY_NAME
                if not entry_path.exists():
                    diagnostics.append(
                        _diagnostic(
                            "missing_entry",
                            source,
                            package_root,
                            "Skill 目录缺少 SKILL.md。",
                            skill_name=package_root.name,
                        )
                    )
                    continue
                if entry_path.is_symlink() or not entry_path.is_file():
                    diagnostics.append(
                        _diagnostic(
                            "invalid_entry",
                            source,
                            entry_path,
                            "Skill 入口必须是普通文件。",
                            skill_name=package_root.name,
                        )
                    )
                    continue
                # 候选发现只看一层目录和文件元数据，不提前读取 SOP 正文。
                candidates.append(
                    SkillCandidate(
                        source=source,
                        package_root=package_root,
                        entry_path=entry_path,
                        fingerprint=_fingerprint(package_root, entry_path),
                    )
                )
        return SkillScanResult(candidates=tuple(candidates), diagnostics=tuple(diagnostics))

    def load(self, candidate: SkillCandidate) -> SkillDefinition:
        try:
            entry_size = candidate.entry_path.stat().st_size
        except OSError as exc:
            raise SkillParseError("entry unavailable") from exc
        if entry_size > MAX_SKILL_FILE_BYTES:
            raise SkillParseError("entry too large")

        text = _read_utf8(candidate.entry_path, parse_error=True)
        metadata_text, body = _split_frontmatter(text)
        if len(metadata_text.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
            raise SkillParseError("frontmatter too large")

        raw_metadata = _parse_metadata_yaml(metadata_text)
        metadata = _build_metadata(candidate.package_root.name, raw_metadata)
        instruction = body.strip()
        if not instruction:
            raise SkillParseError("body must be non-empty")
        resources = _list_resources(candidate.package_root, candidate.entry_path)
        if len(resources) > MAX_RESOURCE_COUNT:
            raise SkillParseError("too many resources")
        revision = _revision(text, resources)
        return SkillDefinition(
            metadata=metadata,
            instruction=instruction,
            source=candidate.source,
            entry_path=candidate.entry_path,
            package_root=candidate.package_root,
            resources=resources,
            revision=revision,
        )

    def read_resource(self, definition: SkillDefinition, relative_path: str) -> str:
        normalized = _normalize_resource_path(relative_path)
        package_root = definition.package_root.resolve()
        candidate = definition.package_root / Path(*normalized.parts)

        if candidate.is_symlink():
            raise SkillResourceError("resource is symlink")
        if not candidate.exists():
            raise SkillResourceError("unknown resource")
        if not candidate.is_file():
            raise SkillResourceError("resource is not a file")

        resolved = candidate.resolve()
        # 真实路径边界检查防止符号链接或平台路径语义逃逸到 Skill 包外。
        if package_root != resolved and package_root not in resolved.parents:
            raise SkillResourceError("resource escapes package")
        posix_path = normalized.as_posix()
        if posix_path not in definition.resources:
            raise SkillResourceError("unknown resource")
        if candidate.stat().st_size > MAX_RESOURCE_BYTES:
            raise SkillResourceError("resource too large")
        return _read_utf8(candidate, parse_error=False)

    def _source_roots(self) -> tuple[tuple[SkillSource, Path], ...]:
        return (
            (SkillSource.BUILTIN, self._builtin_root),
            (SkillSource.USER, self._home / ".mycode" / "skills"),
            (SkillSource.PROJECT, self._workspace_root / ".mycode" / "skills"),
        )


def _fingerprint(package_root: Path, entry_path: Path) -> tuple[tuple[str, int, int], ...]:
    items: list[tuple[str, int, int]] = []
    for path in sorted(package_root.rglob("*"), key=lambda child: child.relative_to(package_root).as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        items.append((path.relative_to(package_root).as_posix(), stat.st_size, stat.st_mtime_ns))
    return tuple(items)


def _list_resources(package_root: Path, entry_path: Path) -> tuple[str, ...]:
    resources: list[str] = []
    for path in sorted(package_root.rglob("*"), key=lambda child: child.relative_to(package_root).as_posix()):
        if path == entry_path or path.is_symlink() or not path.is_file():
            continue
        resources.append(path.relative_to(package_root).as_posix())
    return tuple(resources)


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise SkillParseError("frontmatter must start with ---")
    for index in range(1, len(lines)):
        if lines[index] == "---":
            metadata_text = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            return metadata_text, body
    raise SkillParseError("frontmatter must end with ---")


def _parse_metadata_yaml(metadata_text: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(metadata_text)
    except yaml.YAMLError as exc:
        raise SkillParseError("frontmatter yaml is invalid") from exc
    if not isinstance(value, dict):
        raise SkillParseError("frontmatter must be a mapping")
    unknown = sorted(set(value) - _METADATA_FIELDS)
    if unknown:
        raise SkillParseError(f"unknown frontmatter fields: {', '.join(unknown)}")
    missing = sorted({"name", "description", "allowed_tools", "mode"} - set(value))
    if missing:
        raise SkillParseError(f"missing frontmatter fields: {', '.join(missing)}")
    return value


def _build_metadata(directory_name: str, raw: dict[str, Any]) -> SkillMetadata:
    if raw["name"] != directory_name:
        raise SkillParseError("skill name must match directory")
    allowed_tools = raw["allowed_tools"]
    if not isinstance(allowed_tools, list):
        raise SkillParseError("allowed_tools must be a list")
    mode = _enum_value(SkillMode, raw["mode"], "mode")
    context = _build_context(raw.get("context"), mode)
    try:
        return SkillMetadata(
            name=raw["name"],
            description=raw["description"],
            allowed_tools=tuple(allowed_tools),
            mode=mode,
            context=context,
            model=raw.get("model"),
        )
    except ValueError as exc:
        raise SkillParseError(str(exc)) from exc


def _build_context(value: Any, mode: SkillMode) -> SkillContextPolicy | None:
    if mode is SkillMode.SHARED:
        if value is not None:
            raise SkillParseError("shared context must be empty")
        return None
    if not isinstance(value, dict):
        raise SkillParseError("isolated context is required")
    unknown = sorted(set(value) - {"strategy", "turns"})
    if unknown:
        raise SkillParseError(f"unknown context fields: {', '.join(unknown)}")
    if "strategy" not in value:
        raise SkillParseError("context strategy is required")
    strategy = _enum_value(SkillContextStrategy, value["strategy"], "context strategy")
    turns = value.get("turns", 0)
    if strategy is SkillContextStrategy.RECENT and "turns" not in value:
        raise SkillParseError("recent context turns are required")
    try:
        return SkillContextPolicy(strategy=strategy, turns=turns)
    except ValueError as exc:
        raise SkillParseError(str(exc)) from exc


def _enum_value(enum_type, value: Any, label: str):
    try:
        return enum_type(value)
    except ValueError as exc:
        raise SkillParseError(f"invalid {label}: {value}") from exc


def _normalize_resource_path(relative_path: str) -> PurePosixPath:
    if not isinstance(relative_path, str) or not relative_path:
        raise SkillResourceError("resource path is required")
    if Path(relative_path).is_absolute():
        raise SkillResourceError("resource path must be relative")
    normalized = PurePosixPath(relative_path.replace("\\", "/"))
    if normalized.is_absolute() or any(part in ("", ".", "..") for part in normalized.parts):
        raise SkillResourceError("resource path is unsafe")
    return normalized


def _read_utf8(path: Path, *, parse_error: bool) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        error_type = SkillParseError if parse_error else SkillResourceError
        raise error_type("file must be valid UTF-8") from exc
    except OSError as exc:
        error_type = SkillParseError if parse_error else SkillResourceError
        raise error_type("file unavailable") from exc


def _revision(entry_text: str, resources: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    digest.update(entry_text.encode("utf-8"))
    for resource in resources:
        digest.update(b"\0")
        digest.update(resource.encode("utf-8"))
    return digest.hexdigest()


def _diagnostic(
    code: str,
    source: SkillSource,
    path: Path,
    message: str,
    *,
    skill_name: str | None = None,
) -> SkillDiagnostic:
    return SkillDiagnostic(
        code=code,
        source=source,
        path=str(path),
        message=message,
        skill_name=skill_name,
    )
