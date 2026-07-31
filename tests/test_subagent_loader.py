from pathlib import Path

import pytest

from mycode.subagent.loader import AgentRoleLoader
from mycode.subagent.models import (
    AgentModelTier,
    AgentPermissionMode,
    AgentRoleSource,
)


KNOWN_TOOLS = {"read_file", "find_files", "search_code", "edit_file", "Agent"}


def yaml_scalar(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def yaml_list(field_name: str, values: list[str]) -> list[str]:
    if not values:
        return [f"{field_name}: []"]
    return [f"{field_name}:", *(f"  - {yaml_scalar(value)}" for value in values)]


def write_role(
    path: Path,
    *,
    name: str,
    description: str = "测试角色",
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
    model: str = "inherit",
    max_rounds: int | str = 8,
    permission_mode: str = "strict",
    body: str = "请非交互地完成任务。",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    allowed = ["read_file"] if allowed_tools is None else allowed_tools
    denied = ["Agent"] if denied_tools is None else denied_tools
    path.write_text(
        "\n".join(
            [
                "---",
                f"name: {yaml_scalar(name)}",
                f"description: {yaml_scalar(description)}",
                *yaml_list("allowed_tools", allowed),
                *yaml_list("denied_tools", denied),
                f"model: {yaml_scalar(model)}",
                f"max_rounds: {max_rounds}",
                f"permission_mode: {yaml_scalar(permission_mode)}",
                "---",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )


def collect(loader: AgentRoleLoader):
    candidates = loader.load()
    definitions = [candidate.definition for candidate in candidates if candidate.definition]
    diagnostics = [
        diagnostic
        for candidate in candidates
        for diagnostic in candidate.diagnostics
    ]
    return candidates, definitions, diagnostics


def test_loader_scans_project_user_builtin_and_plugin_sources(tmp_path):
    project = tmp_path / "project"
    home = tmp_path / "home"
    builtin = tmp_path / "builtin"
    plugin = tmp_path / "plugin"
    write_role(project / ".mycode" / "agents" / "project_role.md", name="project_role")
    write_role(home / ".mycode" / "agents" / "user_role.md", name="user_role")
    write_role(builtin / "builtin_role.md", name="builtin_role")
    write_role(plugin / "plugin_role.md", name="plugin_role")

    loader = AgentRoleLoader(
        project_root=project,
        home=home,
        builtin_dir=builtin,
        plugin_dirs=(plugin,),
        known_tool_names=KNOWN_TOOLS,
    )

    _, definitions, diagnostics = collect(loader)

    assert diagnostics == []
    assert [(definition.metadata.name, definition.source) for definition in definitions] == [
        ("plugin_role", AgentRoleSource.PLUGIN),
        ("builtin_role", AgentRoleSource.BUILTIN),
        ("user_role", AgentRoleSource.USER),
        ("project_role", AgentRoleSource.PROJECT),
    ]


def test_loader_parses_valid_frontmatter_body_path_and_revision(tmp_path):
    project = tmp_path / "project"
    role_path = project / ".mycode" / "agents" / "explore.md"
    write_role(
        role_path,
        name="explore",
        description="只读探索",
        allowed_tools=["*"],
        denied_tools=["Agent"],
        model="sonnet",
        max_rounds=12,
        permission_mode="default",
        body="请读取事实并给出有界结论。",
    )

    loader = AgentRoleLoader(
        project_root=project,
        home=tmp_path / "home",
        builtin_dir=tmp_path / "builtin",
        known_tool_names=KNOWN_TOOLS,
    )

    _, definitions, diagnostics = collect(loader)

    assert diagnostics == []
    [definition] = definitions
    assert definition.metadata.name == "explore"
    assert definition.metadata.description == "只读探索"
    assert definition.metadata.allowed_tools == ("*",)
    assert definition.metadata.denied_tools == ("Agent",)
    assert definition.metadata.model is AgentModelTier.SONNET
    assert definition.metadata.max_rounds == 12
    assert definition.metadata.permission_mode is AgentPermissionMode.DEFAULT
    assert definition.instruction == "请读取事实并给出有界结论。"
    assert definition.entry_path == role_path
    assert len(definition.revision) == 64


@pytest.mark.parametrize(
    ("filename", "name", "body", "overrides", "expected_code"),
    [
        ("wrong_file.md", "right_name", "正文", {}, "role_name_mismatch"),
        ("missing.md", "missing", "正文", {"description": None}, "missing_field"),
        ("empty_body.md", "empty_body", "", {}, "empty_body"),
        ("bad_model.md", "bad_model", "正文", {"model": "tiny"}, "invalid_model"),
        ("bad_permission.md", "bad_permission", "正文", {"permission_mode": "root"}, "invalid_permission_mode"),
        ("bad_rounds.md", "bad_rounds", "正文", {"max_rounds": 0}, "invalid_max_rounds"),
    ],
)
def test_loader_reports_invalid_role_files(tmp_path, filename, name, body, overrides, expected_code):
    project = tmp_path / "project"
    role_path = project / ".mycode" / "agents" / filename
    kwargs = {
        "name": name,
        "body": body,
        "description": "描述",
        "model": "inherit",
        "max_rounds": 8,
        "permission_mode": "strict",
    }
    kwargs.update({key: value for key, value in overrides.items() if value is not None})
    write_role(role_path, **kwargs)
    if "description" in overrides and overrides["description"] is None:
        text = role_path.read_text(encoding="utf-8")
        role_path.write_text(
            text.replace(f"description: {yaml_scalar('描述')}\n", ""),
            encoding="utf-8",
        )

    loader = AgentRoleLoader(
        project_root=project,
        home=tmp_path / "home",
        builtin_dir=tmp_path / "builtin",
        known_tool_names=KNOWN_TOOLS,
    )

    candidates, definitions, diagnostics = collect(loader)

    assert definitions == []
    assert len(candidates) == 1
    assert diagnostics[0].code == expected_code
    assert diagnostics[0].source is AgentRoleSource.PROJECT
    assert diagnostics[0].path == str(role_path)


def test_loader_reports_size_limits_before_full_parse(tmp_path):
    project = tmp_path / "project"
    agents_dir = project / ".mycode" / "agents"
    agents_dir.mkdir(parents=True)
    oversized = agents_dir / "oversized.md"
    oversized.write_text("x" * (128 * 1024 + 1), encoding="utf-8")

    huge_frontmatter = agents_dir / "huge_frontmatter.md"
    huge_frontmatter.write_text(
        "---\n" + ("x" * (16 * 1024 + 1)) + "\n---\n正文\n",
        encoding="utf-8",
    )

    loader = AgentRoleLoader(
        project_root=project,
        home=tmp_path / "home",
        builtin_dir=tmp_path / "builtin",
        known_tool_names=KNOWN_TOOLS,
    )

    _, definitions, diagnostics = collect(loader)

    assert definitions == []
    assert [diagnostic.code for diagnostic in diagnostics] == [
        "frontmatter_too_large",
        "role_file_too_large",
    ]


@pytest.mark.parametrize(
    ("allowed_tools", "denied_tools", "expected_code"),
    [
        ([], ["Agent"], None),
        (["*"], ["Agent"], None),
        (["read_file", "search_code"], ["Agent"], None),
        (["*", "read_file"], ["Agent"], "invalid_allowed_tools"),
        (["read_file", "read_file"], ["Agent"], "duplicate_tool"),
        (["read_file"], ["*"], "invalid_denied_tools"),
        (["unknown_tool"], ["Agent"], "unknown_tool"),
    ],
)
def test_loader_validates_allowed_and_denied_tools(
    tmp_path,
    allowed_tools,
    denied_tools,
    expected_code,
):
    project = tmp_path / "project"
    write_role(
        project / ".mycode" / "agents" / "tools.md",
        name="tools",
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
    )
    loader = AgentRoleLoader(
        project_root=project,
        home=tmp_path / "home",
        builtin_dir=tmp_path / "builtin",
        known_tool_names=KNOWN_TOOLS,
    )

    _, definitions, diagnostics = collect(loader)

    if expected_code is None:
        assert diagnostics == []
        assert definitions[0].metadata.allowed_tools == tuple(allowed_tools)
    else:
        assert definitions == []
        assert diagnostics[0].code == expected_code
