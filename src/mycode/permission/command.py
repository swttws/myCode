from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from mycode.permission.models import CommandAssessment, PermissionEffect


_DOWNLOADERS = {
    "curl",
    "wget",
    "iwr",
    "irm",
    "invoke-webrequest",
    "invoke-restmethod",
}
_EXECUTORS = {
    "sh",
    "bash",
    "python",
    "python3",
    "node",
    "perl",
    "ruby",
    "cmd",
    "powershell",
    "pwsh",
    "iex",
    "invoke-expression",
    "eval",
}
_UNKNOWN_SHELLS = {"zsh", "fish", "ksh", "csh", "tcsh", "dash"}
_ENCODED_FLAGS = {"-enc", "-encodedcommand", "/encodedcommand"}
_MAX_COMMAND_LENGTH = 32768


class _AmbiguousCommand(ValueError):
    pass


@dataclass(frozen=True)
class _CommandChain:
    fragments: tuple[str, ...]
    operators: tuple[str, ...]


@dataclass(frozen=True)
class _NestedCommand:
    command: str
    platform: str


class CommandAnalyzer:
    def __init__(
        self,
        workspace_root: str | Path,
        *,
        home: str | Path | None = None,
        platform: str | None = None,
        max_depth: int = 3,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._home = Path(home).expanduser().resolve() if home is not None else Path.home().resolve()
        self._platform = _normalize_platform(platform)
        self._max_depth = max_depth

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    def assess(self, command: str) -> CommandAssessment:
        try:
            return self._assess(command, self._platform, depth=0)
        except Exception:
            # 分析器自身异常也不能把未知命令当作安全命令放行。
            return _ambiguous()

    def _assess(self, command: str, platform: str, *, depth: int) -> CommandAssessment:
        if not isinstance(command, str) or not command.strip():
            return _ambiguous()
        if len(command) > _MAX_COMMAND_LENGTH or "\x00" in command:
            return _ambiguous()
        if platform == "posix" and ("$(" in command or "`" in command):
            return _ambiguous()

        try:
            chain = _split_chain(command, platform)
            tokenized = tuple(_tokenize(fragment, platform) for fragment in chain.fragments)
        except _AmbiguousCommand:
            return _ambiguous()
        if any(not tokens for tokens in tokenized):
            return _ambiguous()
        if any(any(token.lower() in _ENCODED_FLAGS for token in tokens) for tokens in tokenized):
            return _ambiguous()

        if _downloads_and_executes(tokenized, chain.operators):
            return _forbidden_download()

        result = _safe()
        for tokens in tokenized:
            nested = _nested_command(tokens)
            if nested is not None:
                if depth >= self._max_depth:
                    return _ambiguous()
                result = _stronger(result, self._assess(nested.command, nested.platform, depth=depth + 1))
                if result.effect is PermissionEffect.FORBIDDEN:
                    return result
            elif _basename(tokens[0]) in _UNKNOWN_SHELLS:
                return _ambiguous()

            fragment_result = self._assess_tokens(tokens, platform)
            result = _stronger(result, fragment_result)
            if result.effect is PermissionEffect.FORBIDDEN:
                return result
        return result

    def _assess_tokens(self, tokens: tuple[str, ...], platform: str) -> CommandAssessment:
        executable = _basename(tokens[0])
        lowered = tuple(token.lower() for token in tokens)

        if (
            executable.startswith("mkfs")
            or executable in {"format", "diskpart", "format-volume", "clear-disk"}
            or (executable == "dd" and any(token.lower().startswith("of=/dev/") for token in tokens[1:]))
        ):
            return CommandAssessment(
                PermissionEffect.FORBIDDEN,
                "disk_destruction",
                "forbidden_disk_operation",
                "检测到不可逆的磁盘破坏操作，已禁止执行。",
            )

        delete_result = self._assess_delete(tokens, platform)
        if delete_result is not None:
            return delete_result

        if executable == "git" and (
            "clean" in lowered[1:]
            or ("reset" in lowered[1:] and "--hard" in lowered[1:])
            or ("checkout" in lowered[1:] and "-f" in lowered[1:])
            or ("restore" in lowered[1:] and "--worktree" in lowered[1:])
        ):
            return _ask("version_control", "risky_git_operation", "该版本控制操作可能丢失工作区内容，需要确认。")

        if _is_package_install(executable, lowered):
            return _ask("package_install", "risky_package_install", "软件包安装会修改本地环境，需要确认。")
        if executable in _DOWNLOADERS or _is_network_git(executable, lowered) or executable in {
            "scp",
            "ssh",
            "sftp",
            "ftp",
        }:
            return _ask("network_access", "risky_network_access", "该命令会访问外部网络，需要确认。")
        if executable in {"sudo", "runas"} or (
            executable == "start-process" and "-verb" in lowered and "runas" in lowered
        ):
            return _ask("privilege", "risky_privilege_operation", "该命令可能提升权限，需要确认。")
        if executable in {"chmod", "chown", "icacls", "takeown"}:
            return _ask("permission_change", "risky_permission_change", "该命令会修改权限或所有权，需要确认。")
        if executable in {
            "systemctl",
            "service",
            "sc",
            "start-service",
            "stop-service",
            "restart-service",
            "schtasks",
        }:
            return _ask("service_management", "risky_service_management", "该命令会修改服务或计划任务，需要确认。")
        if executable in {"kill", "pkill", "killall", "taskkill", "stop-process"}:
            return _ask("process_management", "risky_process_management", "该命令会终止进程，需要确认。")
        return _safe()

    def _assess_delete(self, tokens: tuple[str, ...], platform: str) -> CommandAssessment | None:
        executable = _basename(tokens[0])
        delete_names = {"rm", "rmdir", "del", "erase", "remove-item", "ri"}
        if executable not in delete_names:
            return None

        lowered = tuple(token.lower() for token in tokens[1:])
        recursive = any(
            token in {"-r", "-rf", "-fr", "--recursive", "/s", "-recurse"}
            or (token.startswith("-") and "r" in token[1:])
            for token in lowered
        )
        targets = _delete_targets(tokens[1:], platform)
        if not targets:
            return _ambiguous()
        for target in targets:
            if recursive and self._is_protected_target(target):
                return CommandAssessment(
                    PermissionEffect.FORBIDDEN,
                    "destructive_system",
                    "forbidden_destructive_command",
                    "检测到针对受保护根目录或系统目录的破坏性删除，已禁止执行。",
                )
        return _ask("workspace_delete", "risky_workspace_delete", "删除操作可能造成数据丢失，需要确认。")

    def _is_protected_target(self, target: str) -> bool:
        raw = target.strip().strip('"\'')
        upper = raw.upper()
        if raw in {"/", "~", "$HOME", "${HOME}"} or upper in {
            "%USERPROFILE%",
            "$ENV:USERPROFILE",
        }:
            return True
        normalized = _path_text(raw)
        if re.fullmatch(r"[a-z]:", normalized):
            return True

        workspace = _path_text(str(self._workspace_root))
        home = _path_text(str(self._home))
        if normalized in {workspace, home}:
            return True

        system_roots = (
            "/etc",
            "/usr",
            "/bin",
            "/sbin",
            "/var",
            "/system",
            "/library",
            "c:/windows",
            "c:/program files",
            "c:/programdata",
        )
        return any(normalized == root or normalized.startswith(root + "/") for root in system_roots)


def _normalize_platform(platform: str | None) -> str:
    if platform is None:
        return "windows" if os.name == "nt" else "posix"
    lowered = platform.lower()
    if lowered in {"windows", "cmd", "nt"}:
        return "windows"
    if lowered in {"powershell", "pwsh"}:
        return "powershell"
    if lowered in {"posix", "sh", "bash"}:
        return "posix"
    raise ValueError(f"unknown shell platform: {platform}")


def _split_chain(command: str, platform: str) -> _CommandChain:
    fragments: list[str] = []
    operators: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        character = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if _is_escape(character, platform, quote):
            escaped = True
            index += 1
            continue
        if quote is not None:
            if character == quote:
                if platform == "powershell" and quote == "'" and index + 1 < len(command) and command[index + 1] == "'":
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            index += 1
            continue

        operator: str | None = None
        if command.startswith("&&", index) or command.startswith("||", index):
            operator = command[index : index + 2]
        elif character in {"|", ";", "\n", "&"}:
            operator = character
        if operator is not None:
            fragment = command[start:index].strip()
            if not fragment:
                raise _AmbiguousCommand("empty command fragment")
            fragments.append(fragment)
            operators.append(operator)
            index += len(operator)
            start = index
            continue
        index += 1

    if quote is not None or escaped:
        raise _AmbiguousCommand("unterminated quote or escape")
    final = command[start:].strip()
    if not final:
        raise _AmbiguousCommand("empty final command fragment")
    fragments.append(final)
    return _CommandChain(tuple(fragments), tuple(operators))


def _is_escape(character: str, platform: str, quote: str | None) -> bool:
    if platform == "posix":
        return character == "\\" and quote != "'"
    if platform == "windows":
        return character == "^"
    return character == "`"


def _tokenize(fragment: str, platform: str) -> tuple[str, ...]:
    if platform == "posix":
        try:
            return tuple(shlex.split(fragment, posix=True))
        except ValueError as exc:
            raise _AmbiguousCommand("invalid POSIX quoting") from exc

    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(fragment):
        character = fragment[index]
        if escaped:
            current.append(character)
            escaped = False
            index += 1
            continue
        if _is_escape(character, platform, quote):
            escaped = True
            index += 1
            continue
        if quote is not None:
            if character == quote:
                quote = None
            else:
                current.append(character)
            index += 1
            continue
        if character in {'"', "'"}:
            quote = character
        elif character.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(character)
        index += 1
    if quote is not None or escaped:
        raise _AmbiguousCommand("invalid shell quoting")
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _nested_command(tokens: tuple[str, ...]) -> _NestedCommand | None:
    executable = _basename(tokens[0])
    lowered = tuple(token.lower() for token in tokens)
    if executable == "cmd" and "/c" in lowered:
        index = lowered.index("/c")
        return _NestedCommand(" ".join(tokens[index + 1 :]), "windows")
    if executable in {"powershell", "pwsh"}:
        for flag in ("-command", "-c"):
            if flag in lowered:
                index = lowered.index(flag)
                return _NestedCommand(" ".join(tokens[index + 1 :]), "powershell")
    if executable in {"sh", "bash"} and "-c" in lowered:
        index = lowered.index("-c")
        return _NestedCommand(" ".join(tokens[index + 1 :]), "posix")
    return None


def _downloads_and_executes(
    tokenized: tuple[tuple[str, ...], ...], operators: tuple[str, ...]
) -> bool:
    downloaded_files: set[str] = set()
    for index, tokens in enumerate(tokenized):
        executable = _basename(tokens[0])
        if executable in _DOWNLOADERS:
            if index < len(operators) and operators[index] == "|":
                if index + 1 < len(tokenized) and _basename(tokenized[index + 1][0]) in _EXECUTORS:
                    return True
            output = _download_output(tokens)
            if output is not None:
                downloaded_files.add(_path_key(output))
            continue

        if downloaded_files and _invokes_downloaded_file(tokens, downloaded_files):
            return True
    return False


def _download_output(tokens: tuple[str, ...]) -> str | None:
    lowered = tuple(token.lower() for token in tokens)
    for index, token in enumerate(lowered[:-1]):
        if token in {"-o", "--output", "-outfile"}:
            value = tokens[index + 1]
            if value != "-":
                return value
    for original, lowered_token in zip(tokens, lowered):
        for prefix in ("--output=", "-outfile:"):
            if lowered_token.startswith(prefix):
                return original[len(prefix) :]
    return None


def _invokes_downloaded_file(tokens: tuple[str, ...], downloaded_files: set[str]) -> bool:
    executable = _basename(tokens[0])
    if executable in _EXECUTORS:
        return any(_path_key(token) in downloaded_files for token in tokens[1:])
    return _path_key(tokens[0]) in downloaded_files


def _delete_targets(tokens: tuple[str, ...], platform: str) -> tuple[str, ...]:
    ignored = {"/s", "/q", "/f", "-recurse", "-force", "-r", "-rf", "-fr", "--recursive"}
    targets: list[str] = []
    skip_next = False
    for token in tokens:
        lowered = token.lower()
        if skip_next:
            targets.append(token)
            skip_next = False
            continue
        if lowered in {"-path", "-literalpath"}:
            skip_next = True
            continue
        if lowered in ignored:
            continue
        if platform == "posix" and lowered.startswith("-"):
            continue
        if platform != "posix" and lowered.startswith("-"):
            continue
        targets.append(token)
    return tuple(targets)


def _is_package_install(executable: str, lowered: tuple[str, ...]) -> bool:
    if executable in {"pip", "pip3", "npm", "yarn", "pnpm", "cargo", "gem"}:
        return any(token in {"install", "add", "i"} for token in lowered[1:])
    if executable in {"python", "python3"} and len(lowered) >= 4:
        return lowered[1:4] == ("-m", "pip", "install")
    return False


def _is_network_git(executable: str, lowered: tuple[str, ...]) -> bool:
    return executable == "git" and any(
        token in {"clone", "fetch", "pull", "push", "ls-remote"} for token in lowered[1:]
    )


def _basename(value: str) -> str:
    normalized = value.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return normalized[:-4] if normalized.endswith(".exe") else normalized


def _path_text(value: str) -> str:
    normalized = os.path.normcase(value.replace("\\", "/")).replace("\\", "/")
    return normalized.rstrip("/") or "/"


def _path_key(value: str) -> str:
    return value.strip().strip('"\'').replace("\\", "/").removeprefix("./").lower()


def _stronger(first: CommandAssessment, second: CommandAssessment) -> CommandAssessment:
    rank = {
        PermissionEffect.ALLOW: 0,
        PermissionEffect.ASK: 1,
        PermissionEffect.DENY: 2,
        PermissionEffect.FORBIDDEN: 3,
    }
    return second if rank[second.effect] > rank[first.effect] else first


def _safe() -> CommandAssessment:
    return CommandAssessment(PermissionEffect.ALLOW, None, None, None)


def _ask(category: str, reason_code: str, message: str) -> CommandAssessment:
    return CommandAssessment(PermissionEffect.ASK, category, reason_code, message)


def _ambiguous() -> CommandAssessment:
    # 无法可靠理解的结构交给用户判断，绝不能因为解析失败而当作普通安全命令。
    return _ask("ambiguous", "command_ambiguous", "命令结构无法可靠解析，需要人工确认。")


def _forbidden_download() -> CommandAssessment:
    return CommandAssessment(
        PermissionEffect.FORBIDDEN,
        "download_execute",
        "forbidden_download_execute",
        "检测到远程内容下载后直接执行，已禁止执行。",
    )
