import pytest

from mycode.subagent.catalog import AgentCatalog
from mycode.subagent.loader import AgentRoleLoader
from mycode.subagent.models import AgentRoleSource

from tests.test_subagent_loader import KNOWN_TOOLS, write_role


def make_catalog(tmp_path, *, project=None, home=None, builtin=None, plugin=None):
    loader = AgentRoleLoader(
        project_root=project or tmp_path / "project",
        home=home or tmp_path / "home",
        builtin_dir=builtin or tmp_path / "builtin",
        plugin_dirs=(() if plugin is None else (plugin,)),
        known_tool_names=KNOWN_TOOLS,
    )
    return AgentCatalog(loader)


def test_catalog_applies_project_user_builtin_plugin_precedence(tmp_path):
    project = tmp_path / "project"
    home = tmp_path / "home"
    builtin = tmp_path / "builtin"
    plugin = tmp_path / "plugin"
    write_role(plugin / "general.md", name="general", body="plugin")
    write_role(builtin / "general.md", name="general", body="builtin")
    write_role(home / ".mycode" / "agents" / "general.md", name="general", body="user")
    write_role(project / ".mycode" / "agents" / "general.md", name="general", body="project")

    snapshot = make_catalog(
        tmp_path,
        project=project,
        home=home,
        builtin=builtin,
        plugin=plugin,
    ).initialize()

    [definition] = snapshot.definitions
    assert definition.source is AgentRoleSource.PROJECT
    assert definition.instruction == "project"
    assert snapshot.diagnostics == ()


def test_catalog_falls_back_when_higher_priority_candidate_is_invalid(tmp_path):
    project = tmp_path / "project"
    home = tmp_path / "home"
    builtin = tmp_path / "builtin"
    write_role(project / ".mycode" / "agents" / "general.md", name="wrong_name", body="bad")
    write_role(home / ".mycode" / "agents" / "general.md", name="general", body="user")
    write_role(builtin / "general.md", name="general", body="builtin")

    snapshot = make_catalog(tmp_path, project=project, home=home, builtin=builtin).initialize()

    [definition] = snapshot.definitions
    assert definition.source is AgentRoleSource.USER
    assert definition.instruction == "user"
    assert [diagnostic.code for diagnostic in snapshot.diagnostics] == [
        "role_name_mismatch"
    ]


def test_catalog_sorts_definitions_and_diagnostics_stably(tmp_path):
    project = tmp_path / "project"
    write_role(project / ".mycode" / "agents" / "zeta.md", name="zeta")
    write_role(project / ".mycode" / "agents" / "alpha.md", name="alpha")
    write_role(project / ".mycode" / "agents" / "bad_b.md", name="wrong_b")
    write_role(project / ".mycode" / "agents" / "bad_a.md", name="wrong_a")

    snapshot = make_catalog(tmp_path, project=project).initialize()

    assert [definition.metadata.name for definition in snapshot.definitions] == [
        "alpha",
        "zeta",
    ]
    assert [diagnostic.path for diagnostic in snapshot.diagnostics] == sorted(
        diagnostic.path for diagnostic in snapshot.diagnostics
    )


def test_catalog_initializes_once_and_snapshot_does_not_refresh(tmp_path):
    project = tmp_path / "project"
    write_role(project / ".mycode" / "agents" / "general.md", name="general", body="first")
    catalog = make_catalog(tmp_path, project=project)

    first = catalog.initialize()
    write_role(project / ".mycode" / "agents" / "later.md", name="later", body="later")
    second = catalog.snapshot()

    assert first is second
    assert [definition.metadata.name for definition in second.definitions] == ["general"]
    with pytest.raises(RuntimeError, match="already initialized"):
        catalog.initialize()


def test_catalog_get_returns_definition_or_stable_key_error(tmp_path):
    project = tmp_path / "project"
    write_role(project / ".mycode" / "agents" / "general.md", name="general")
    catalog = make_catalog(tmp_path, project=project)
    catalog.initialize()

    assert catalog.get("general").metadata.name == "general"
    with pytest.raises(KeyError, match="missing"):
        catalog.get("missing")
