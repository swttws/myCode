from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from mycode.memory.models import (
    InstructionBlock,
    InstructionLayer,
    InstructionLoadResult,
    MemoryDiagnostic,
)
from mycode.memory.paths import MemoryPathError, MemoryPaths


@dataclass(frozen=True)
class _SourceSpec:
    layer: InstructionLayer
    path: Path
    priority: int


class InstructionLoader:
    def __init__(self, *, paths: MemoryPaths, max_include_depth: int = 5) -> None:
        if max_include_depth < 1:
            raise ValueError("max_include_depth must be positive")
        self._paths = paths
        self._max_include_depth = max_include_depth

    def load(self) -> InstructionLoadResult:
        blocks: list[InstructionBlock] = []
        diagnostics: list[MemoryDiagnostic] = []
        for source in self._source_specs():
            if not source.path.exists():
                continue
            try:
                resolved_path = source.path.resolve(strict=True)
            except OSError as exc:
                diagnostics.append(
                    MemoryDiagnostic(
                        code="instruction_unreadable",
                        message=str(exc),
                        path=str(source.path),
                    )
                )
                continue

            text = self._load_text(
                resolved_path,
                diagnostics=diagnostics,
                line_path=str(resolved_path),
                depth=0,
                stack=(resolved_path,),
            )
            blocks.append(
                InstructionBlock(
                    layer=source.layer,
                    path=str(resolved_path),
                    priority=source.priority,
                    text=text,
                    sha256=_sha256(text),
                )
            )

        rendered_text = _render_blocks(blocks)
        return InstructionLoadResult(blocks=tuple(blocks), rendered_text=rendered_text, diagnostics=tuple(diagnostics))

    def _source_specs(self) -> tuple[_SourceSpec, ...]:
        return (
            _SourceSpec(
                layer=InstructionLayer.PROJECT_ROOT,
                path=self._paths.workspace_root / "mycode.md",
                priority=100,
            ),
            _SourceSpec(
                layer=InstructionLayer.PROJECT_DIRECTORY,
                path=self._paths.workspace_root / ".mycode" / "instructions.md",
                priority=200,
            ),
            _SourceSpec(
                layer=InstructionLayer.USER,
                path=self._paths.home / ".mycode" / "instructions.md",
                priority=300,
            ),
        )

    def _load_text(
        self,
        path: Path,
        *,
        diagnostics: list[MemoryDiagnostic],
        line_path: str,
        depth: int,
        stack: tuple[Path, ...],
    ) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            diagnostics.append(
                MemoryDiagnostic(
                    code="instruction_unreadable",
                    message=str(exc),
                    path=line_path,
                )
            )
            return ""

        rendered_lines: list[str] = []
        for line_number, line in enumerate(text.splitlines()):
            include_target = _parse_include_target(line)
            if include_target is None:
                rendered_lines.append(line)
                continue

            target = path.parent / include_target
            try:
                validated_target = self._validate_include_target(path, target)
            except MemoryPathError as exc:
                diagnostics.append(
                    MemoryDiagnostic(
                        code="include_path_escape",
                        message=str(exc),
                        path=line_path,
                        line=line_number + 1,
                    )
                )
                continue

            try:
                resolved_target = validated_target.resolve(strict=True)
            except FileNotFoundError:
                diagnostics.append(
                    MemoryDiagnostic(
                        code="include_not_found",
                        message=f"include target not found: {include_target}",
                        path=line_path,
                        line=line_number + 1,
                    )
                )
                continue
            except (OSError, UnicodeDecodeError) as exc:
                diagnostics.append(
                    MemoryDiagnostic(
                        code="include_unreadable",
                        message=str(exc),
                        path=line_path,
                        line=line_number + 1,
                    )
                )
                continue

            if resolved_target in stack:
                diagnostics.append(
                    MemoryDiagnostic(
                        code="include_cycle",
                        message=f"include cycle detected: {include_target}",
                        path=line_path,
                        line=line_number + 1,
                    )
                )
                continue

            if depth + 1 > self._max_include_depth:
                diagnostics.append(
                    MemoryDiagnostic(
                        code="include_depth_exceeded",
                        message=f"include depth exceeded: {include_target}",
                        path=line_path,
                        line=line_number + 1,
                    )
                )
                continue

            rendered_lines.append(
                self._load_text(
                    resolved_target,
                    diagnostics=diagnostics,
                    line_path=str(resolved_target),
                    depth=depth + 1,
                    stack=stack + (resolved_target,),
                )
            )

        return "\n".join(rendered_lines)

    def _validate_include_target(self, current_path: Path, target: Path) -> Path:
        if _is_within(current_path, self._paths.home / ".mycode"):
            return self._paths.validate_user_mycode_path(target)
        return self._paths.validate_project_path(target)


def _parse_include_target(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("@include "):
        return None
    target = stripped[len("@include ") :].strip()
    return target or None


def _render_blocks(blocks: tuple[InstructionBlock, ...] | list[InstructionBlock]) -> str:
    rendered_blocks = []
    for block in blocks:
        rendered_blocks.append(
            "\n".join(
                (
                    f"## {block.layer.value}",
                    f"path: {block.path}",
                    f"sha256: {block.sha256}",
                    "",
                    block.text,
                )
            )
        )
    return "\n\n".join(rendered_blocks)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError):
        return False
    return True
