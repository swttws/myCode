import asyncio
import hashlib
import os
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from mycode.permission.config import PermissionStore, load_permission_file
from mycode.permission.models import (
    ArgumentCondition,
    PermissionConfigError,
    PermissionEffect,
    PermissionMode,
    PermissionPersistenceError,
    PermissionRule,
    RuleSource,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).strip() + "\n", encoding="utf-8")


def _rule(rule_id: str, *, source=RuleSource.SESSION, path="src/**") -> PermissionRule:
    return PermissionRule(
        id=rule_id,
        effect=PermissionEffect.ALLOW,
        tool="read_file",
        arguments=(ArgumentCondition("path", path),),
        source=source,
    )


def test_load_permission_file_parses_version_mode_rules_and_scalars(tmp_path):
    path = tmp_path / "permissions.yaml"
    _write(
        path,
        """
        version: 1
        mode: strict
        rules:
          - id: allow-source
            effect: allow
            tool: read_file
            arguments:
              path: src/**
              retries: 2
              recursive: true
        """,
    )

    config = load_permission_file(path, RuleSource.USER_GLOBAL)

    assert config.version == 1
    assert config.mode is PermissionMode.STRICT
    assert config.workspace is None
    assert config.rules == (
        PermissionRule(
            id="allow-source",
            effect=PermissionEffect.ALLOW,
            tool="read_file",
            arguments=(
                ArgumentCondition("path", "src/**"),
                ArgumentCondition("retries", 2),
                ArgumentCondition("recursive", True),
            ),
            source=RuleSource.USER_GLOBAL,
        ),
    )


def test_missing_permission_file_is_an_empty_versioned_config(tmp_path):
    config = load_permission_file(tmp_path / "missing.yaml", RuleSource.USER_GLOBAL)

    assert config.version == 1
    assert config.mode is None
    assert config.rules == ()


@pytest.mark.parametrize(
    "text",
    [
        "version: 2\n",
        "version: 1\nunknown: true\n",
        "version: 1\nmode: unsafe\n",
        "version: 1\nrules:\n  - id: ''\n    effect: allow\n    tool: read_file\n",
        "version: 1\nrules:\n  - id: x\n    effect: forbidden\n    tool: read_file\n",
        "version: 1\nrules:\n  - id: x\n    effect: allow\n    tool: read_*\n",
        "version: 1\nrules:\n  - id: x\n    effect: allow\n    tool: read_file\n    arguments:\n      path: null\n",
        "version: 1\nrules:\n  - id: x\n    effect: allow\n    tool: read_file\n    arguments:\n      path: [src]\n",
        "version: 1\nrules:\n  - id: x\n    effect: allow\n    tool: read_file\n    arguments:\n      path:\n        nested: true\n",
    ],
)
def test_load_permission_file_rejects_invalid_structure(tmp_path, text):
    path = tmp_path / "permissions.yaml"
    _write(path, text)

    with pytest.raises(PermissionConfigError, match="permissions.yaml"):
        load_permission_file(path, RuleSource.USER_GLOBAL)


@pytest.mark.parametrize(
    "extra",
    [
        "mode: permissive",
        "workspace: C:/repo",
        "rules:\n  - id: injected\n    effect: allow\n    tool: run_command",
    ],
)
def test_repository_config_cannot_expand_permissions(tmp_path, extra):
    path = tmp_path / "mycode.permissions.yaml"
    _write(path, f"version: 1\n{extra}\n")

    with pytest.raises(PermissionConfigError, match="仓库"):
        load_permission_file(path, RuleSource.REPOSITORY_PROJECT)


def test_repository_config_accepts_only_deny_and_ask(tmp_path):
    path = tmp_path / "mycode.permissions.yaml"
    _write(
        path,
        """
        version: 1
        rules:
          - id: protect-env
            effect: deny
            tool: write_file
            arguments:
              path: .env*
          - id: review-command
            effect: ask
            tool: run_command
        """,
    )

    config = load_permission_file(path, RuleSource.REPOSITORY_PROJECT)

    assert [rule.effect for rule in config.rules] == [PermissionEffect.DENY, PermissionEffect.ASK]


def test_local_project_config_accepts_workspace_mode_and_allow(tmp_path):
    path = tmp_path / "permissions.yaml"
    _write(
        path,
        """
        version: 1
        workspace: D:/repo
        mode: permissive
        rules:
          - id: local-command
            effect: allow
            tool: run_command
        """,
    )

    config = load_permission_file(path, RuleSource.LOCAL_PROJECT)

    assert config.workspace == "D:/repo"
    assert config.mode is PermissionMode.PERMISSIVE
    assert config.rules[0].source is RuleSource.LOCAL_PROJECT


def test_duplicate_ids_and_exact_condition_conflicts_are_rejected(tmp_path):
    duplicate = tmp_path / "duplicate.yaml"
    _write(
        duplicate,
        """
        version: 1
        rules:
          - id: same
            effect: allow
            tool: read_file
          - id: same
            effect: allow
            tool: write_file
        """,
    )
    conflict = tmp_path / "conflict.yaml"
    _write(
        conflict,
        """
        version: 1
        rules:
          - id: first
            effect: allow
            tool: read_file
          - id: second
            effect: deny
            tool: read_file
        """,
    )

    with pytest.raises(PermissionConfigError, match="重复"):
        load_permission_file(duplicate, RuleSource.USER_GLOBAL)
    with pytest.raises(PermissionConfigError, match="冲突"):
        load_permission_file(conflict, RuleSource.USER_GLOBAL)


def test_permission_store_paths_use_canonical_workspace_sha256(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()

    store = PermissionStore.load(workspace, home=home)

    identity = os.path.normcase(str(workspace.resolve()))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    assert store.paths.user_global == home / ".mycode" / "permissions.yaml"
    assert store.paths.local_project == home / ".mycode" / "projects" / digest / "permissions.yaml"
    assert store.paths.repository_project == workspace / "mycode.permissions.yaml"


def test_permission_store_loads_all_sources_and_validates_local_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    initial = PermissionStore.load(workspace, home=home)
    _write(initial.paths.user_global, "version: 1\nmode: strict\nrules: []")
    _write(
        initial.paths.local_project,
        f"version: 1\nworkspace: {workspace.resolve().as_posix()}\nmode: permissive\nrules: []",
    )
    _write(
        initial.paths.repository_project,
        "version: 1\nrules:\n  - id: repo-deny\n    effect: deny\n    tool: write_file",
    )

    loaded = PermissionStore.load(workspace, home=home)

    assert loaded.effective_mode() == (PermissionMode.PERMISSIVE, RuleSource.LOCAL_PROJECT)
    assert loaded.rules_for(RuleSource.REPOSITORY_PROJECT)[0].id == "repo-deny"

    _write(initial.paths.local_project, "version: 1\nworkspace: D:/another\nrules: []")
    with pytest.raises(PermissionConfigError, match="工作区"):
        PermissionStore.load(workspace, home=home)


def test_effective_mode_and_session_lifecycle_follow_source_priority(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    initial = PermissionStore.load(workspace, home=home)
    _write(initial.paths.user_global, "version: 1\nmode: strict\nrules: []")
    store = PermissionStore.load(workspace, home=home)

    assert store.effective_mode() == (PermissionMode.STRICT, RuleSource.USER_GLOBAL)
    store.set_session_mode(PermissionMode.PERMISSIVE)
    store.add_session_rule(_rule("session-read"))
    store.add_session_rule(_rule("session-read"))
    assert store.effective_mode() == (PermissionMode.PERMISSIVE, RuleSource.SESSION)
    assert store.rules_for(RuleSource.SESSION) == (_rule("session-read"),)

    with pytest.raises(PermissionConfigError, match="规则 ID"):
        store.add_session_rule(_rule("session-read", path="tests/**"))

    store.clear_session()
    assert store.effective_mode() == (PermissionMode.STRICT, RuleSource.USER_GLOBAL)
    assert store.rules_for(RuleSource.SESSION) == ()


def test_persist_local_project_rule_is_atomic_and_does_not_modify_repository(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    repository_text = "version: 1\nrules:\n  - id: repo\n    effect: deny\n    tool: write_file\n"
    (workspace / "mycode.permissions.yaml").write_text(repository_text, encoding="utf-8")
    store = PermissionStore.load(workspace, home=home)
    rule = _rule("local-read", source=RuleSource.LOCAL_PROJECT)

    asyncio.run(store.persist_local_project_rule(rule))
    asyncio.run(store.persist_local_project_rule(rule))

    persisted = yaml.safe_load(store.paths.local_project.read_text(encoding="utf-8"))
    assert persisted["version"] == 1
    assert persisted["workspace"] == workspace.resolve().as_posix()
    assert [item["id"] for item in persisted["rules"]] == ["local-read"]
    assert store.rules_for(RuleSource.LOCAL_PROJECT) == (rule,)
    assert (workspace / "mycode.permissions.yaml").read_text(encoding="utf-8") == repository_text


def test_persist_failure_keeps_original_file_and_memory(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    store = PermissionStore.load(workspace, home=home)
    first = _rule("first", source=RuleSource.LOCAL_PROJECT)
    asyncio.run(store.persist_local_project_rule(first))
    original = store.paths.local_project.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("mycode.permission.config.os.replace", fail_replace)

    with pytest.raises(PermissionPersistenceError, match="保存失败"):
        asyncio.run(
            store.persist_local_project_rule(_rule("second", source=RuleSource.LOCAL_PROJECT))
        )

    assert store.paths.local_project.read_bytes() == original
    assert store.rules_for(RuleSource.LOCAL_PROJECT) == (first,)
    assert list(store.paths.local_project.parent.glob("*.tmp")) == []
