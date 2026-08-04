from pathlib import Path
from textwrap import dedent

import pytest

from mycode.worktree.config import WorktreeConfigLoader
from mycode.worktree.models import WorktreeError, WorktreeRuleType


def test_missing_worktree_config_loads_default_values(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    config = WorktreeConfigLoader().load(repo_root)

    assert config.version == 1
    assert config.rules == ()
    assert config.git_timeout_seconds == 30.0
    assert config.cleanup_interval_seconds == 3600.0
    assert config.expire_after_seconds == 604800.0
    assert config.scan_batch_size == 64
    assert len(config.digest) == 64


def test_valid_worktree_config_loads_rules_in_declared_order(tmp_path: Path):
    repo_root = _repo_with_sources(tmp_path)
    _write_config(
        repo_root,
        """
        version: 1
        git_timeout_seconds: 45
        cleanup:
          interval_seconds: 120
          expire_after_seconds: 86400
          scan_batch_size: 8
        rules:
          - type: copy
            source: config/local.toml
            target: config/local.toml
          - type: ignored_copy
            source: .cache/seed.db
            target: .cache/seed.db
          - type: symlink
            source: vendor
            target: vendor
          - type: hooks
            source: hooks
            target: .git-hooks
        """,
    )

    config = WorktreeConfigLoader().load(repo_root)

    assert config.version == 1
    assert config.git_timeout_seconds == 45.0
    assert config.cleanup_interval_seconds == 120.0
    assert config.expire_after_seconds == 86400.0
    assert config.scan_batch_size == 8
    assert [rule.type for rule in config.rules] == [
        WorktreeRuleType.COPY,
        WorktreeRuleType.IGNORED_COPY,
        WorktreeRuleType.SYMLINK,
        WorktreeRuleType.HOOKS,
    ]
    assert [rule.source for rule in config.rules] == [
        "config/local.toml",
        ".cache/seed.db",
        "vendor",
        "hooks",
    ]
    assert [rule.target for rule in config.rules] == [
        "config/local.toml",
        ".cache/seed.db",
        "vendor",
        ".git-hooks",
    ]
    assert len(config.digest) == 64


def test_worktree_config_digest_is_stable_for_key_order_not_rule_order(tmp_path: Path):
    repo_root = _repo_with_sources(tmp_path)
    _write_config(
        repo_root,
        """
        version: 1
        git_timeout_seconds: 45
        cleanup:
          interval_seconds: 120
          expire_after_seconds: 86400
          scan_batch_size: 8
        rules:
          - type: copy
            source: config/local.toml
            target: config/local.toml
          - type: hooks
            source: hooks
            target: .git-hooks
        """,
    )
    digest = WorktreeConfigLoader().load(repo_root).digest

    _write_config(
        repo_root,
        """
        rules:
          - target: config/local.toml
            source: config/local.toml
            type: copy
          - target: .git-hooks
            type: hooks
            source: hooks
        cleanup:
          scan_batch_size: 8
          expire_after_seconds: 86400
          interval_seconds: 120
        git_timeout_seconds: 45
        version: 1
        """,
    )
    reordered_key_digest = WorktreeConfigLoader().load(repo_root).digest

    _write_config(
        repo_root,
        """
        version: 1
        git_timeout_seconds: 45
        cleanup:
          interval_seconds: 120
          expire_after_seconds: 86400
          scan_batch_size: 8
        rules:
          - type: hooks
            source: hooks
            target: .git-hooks
          - type: copy
            source: config/local.toml
            target: config/local.toml
        """,
    )
    reordered_rule_digest = WorktreeConfigLoader().load(repo_root).digest

    assert reordered_key_digest == digest
    assert reordered_rule_digest != digest


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        ("[]", "mapping"),
        ("version: 2\nrules: []\n", "version"),
        ("version: 1\nunknown: true\nrules: []\n", "未知"),
        (
            """
            version: 1
            rules:
              - type: copy
                source: config/local.toml
                target: config/local.toml
                extra: true
            """,
            "未知",
        ),
        (
            """
            version: 1
            rules:
              - type: unknown
                source: config/local.toml
                target: config/local.toml
            """,
            "type",
        ),
        (
            """
            version: 1
            rules:
              - type: copy
                source: ""
                target: config/local.toml
            """,
            "source",
        ),
        (
            """
            version: 1
            rules:
              - type: copy
                source: /outside.txt
                target: config/local.toml
            """,
            "边界",
        ),
    ],
)
def test_invalid_unknown_and_out_of_boundary_config_fails_closed(
    tmp_path: Path,
    text: str,
    pattern: str,
):
    repo_root = _repo_with_sources(tmp_path)
    _write_config(repo_root, text)

    with pytest.raises(WorktreeError, match=pattern):
        WorktreeConfigLoader().load(repo_root)


def _rules_yaml(count: int) -> str:
    rules = "\n".join(
        f"""
          - type: copy
            source: config/local.toml
            target: generated/file-{index}.toml
        """.rstrip()
        for index in range(count)
    )
    return f"""
    version: 1
    rules:
    {rules}
    """


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        (_rules_yaml(129), "128"),
        (
            """
            version: 1
            rules:
              - type: copy
                source: config/local.toml
                target: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
            """,
            "512",
        ),
        ("version: 1\ngit_timeout_seconds: true\nrules: []\n", "git_timeout_seconds"),
        ("version: 1\ngit_timeout_seconds: 0\nrules: []\n", "git_timeout_seconds"),
        ("version: 1\ngit_timeout_seconds: 121\nrules: []\n", "git_timeout_seconds"),
        ("version: 1\ncleanup: {interval_seconds: 0}\nrules: []\n", "interval_seconds"),
        ("version: 1\ncleanup: {expire_after_seconds: 0}\nrules: []\n", "expire_after_seconds"),
        ("version: 1\ncleanup: {scan_batch_size: 65}\nrules: []\n", "scan_batch_size"),
    ],
)
def test_limit_config_fails_closed(tmp_path: Path, text: str, pattern: str):
    repo_root = _repo_with_sources(tmp_path)
    _write_config(repo_root, text)

    with pytest.raises(WorktreeError, match=pattern):
        WorktreeConfigLoader().load(repo_root)


@pytest.mark.parametrize(
    "text",
    [
        """
        version: 1
        rules:
          - type: copy
            source: config/local.toml
            target: config/local.toml
          - type: symlink
            source: vendor
            target: config/local.toml
        """,
        """
        version: 1
        rules:
          - type: copy
            source: config/local.toml
            target: Config/Local.toml
          - type: symlink
            source: vendor
            target: config/local.toml
        """,
        """
        version: 1
        rules:
          - type: copy
            source: config/local.toml
            target: config
          - type: symlink
            source: vendor
            target: config/local.toml
        """,
        """
        version: 1
        rules:
          - type: hooks
            source: hooks
            target: .git-hooks
          - type: hooks
            source: hooks
            target: .other-hooks
        """,
    ],
)
def test_conflicting_config_targets_fail_closed(tmp_path: Path, text: str):
    repo_root = _repo_with_sources(tmp_path)
    _write_config(repo_root, text)

    with pytest.raises(WorktreeError, match="冲突|hooks"):
        WorktreeConfigLoader().load(repo_root)


def test_config_source_symlink_cycle_fails_closed(tmp_path: Path):
    repo_root = _repo_with_sources(tmp_path)
    cycle = repo_root / "cycle"
    try:
        cycle.symlink_to(cycle, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"platform does not allow symlinks in this test: {exc}")
    _write_config(
        repo_root,
        """
        version: 1
        rules:
          - type: symlink
            source: cycle
            target: cycle
        """,
    )

    with pytest.raises(WorktreeError, match="循环|边界|解析"):
        WorktreeConfigLoader().load(repo_root)


def _repo_with_sources(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / ".mycode").mkdir(parents=True)
    (repo_root / "config").mkdir()
    (repo_root / "config" / "local.toml").write_text("local = true\n", encoding="utf-8")
    (repo_root / ".cache").mkdir()
    (repo_root / ".cache" / "seed.db").write_text("seed\n", encoding="utf-8")
    (repo_root / "vendor").mkdir()
    (repo_root / "hooks").mkdir()
    return repo_root


def _write_config(repo_root: Path, text: str) -> None:
    (repo_root / ".mycode" / "worktree.yaml").write_text(
        dedent(text).strip() + "\n",
        encoding="utf-8",
    )
