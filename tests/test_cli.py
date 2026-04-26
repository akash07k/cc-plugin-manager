import json
from unittest.mock import patch

import pytest

from cc_plugin_manager.cli import ClaudeCli, CliResult, CliNotFoundError, Timeouts
from cc_plugin_manager.data import InstalledPlugin, Plugin


def test_timeouts_defaults_are_sane():
    """Defaults: list queries < install/update; uninstall < install."""
    t = Timeouts()
    assert t.list_query < t.install
    assert t.uninstall <= t.install
    assert t.marketplace_add > 0


def test_timeouts_are_overridable():
    t = Timeouts(install=42.0)
    assert t.install == 42.0
    assert t.list_query == 30.0  # default unchanged


def test_timeouts_from_env_reads_each_field(monkeypatch):
    monkeypatch.setenv("CC_PLUGIN_MANAGER_TIMEOUT_LIST", "10")
    monkeypatch.setenv("CC_PLUGIN_MANAGER_TIMEOUT_INSTALL", "1200")
    monkeypatch.setenv("CC_PLUGIN_MANAGER_TIMEOUT_UPDATE", "1200")
    monkeypatch.setenv("CC_PLUGIN_MANAGER_TIMEOUT_UNINSTALL", "60")
    monkeypatch.setenv("CC_PLUGIN_MANAGER_TIMEOUT_MARKETPLACE", "180.5")
    t = Timeouts.from_env()
    assert t.list_query == 10.0
    assert t.install == 1200.0
    assert t.update == 1200.0
    assert t.uninstall == 60.0
    assert t.marketplace_add == 180.5


def test_timeouts_from_env_keeps_defaults_for_unset_vars(monkeypatch):
    for name in (
        "CC_PLUGIN_MANAGER_TIMEOUT_LIST",
        "CC_PLUGIN_MANAGER_TIMEOUT_INSTALL",
        "CC_PLUGIN_MANAGER_TIMEOUT_UPDATE",
        "CC_PLUGIN_MANAGER_TIMEOUT_UNINSTALL",
        "CC_PLUGIN_MANAGER_TIMEOUT_MARKETPLACE",
    ):
        monkeypatch.delenv(name, raising=False)
    t = Timeouts.from_env()
    assert t == Timeouts()


def test_timeouts_from_env_ignores_invalid_values(monkeypatch):
    """A typo / negative / non-numeric env var keeps the default — never bricks the app."""
    monkeypatch.setenv("CC_PLUGIN_MANAGER_TIMEOUT_INSTALL", "not-a-number")
    monkeypatch.setenv("CC_PLUGIN_MANAGER_TIMEOUT_UPDATE", "-5")
    monkeypatch.setenv("CC_PLUGIN_MANAGER_TIMEOUT_UNINSTALL", "")
    t = Timeouts.from_env()
    defaults = Timeouts()
    assert t.install == defaults.install
    assert t.update == defaults.update
    assert t.uninstall == defaults.uninstall


def test_cli_result_dataclass():
    r = CliResult(
        cmd=["claude", "plugin", "list"],
        returncode=0,
        stdout="",
        stderr="",
        duration=0.1,
        timed_out=False,
    )
    assert r.success is True


def test_cli_result_failure():
    r = CliResult(cmd=[], returncode=2, stdout="", stderr="bad", duration=0.0, timed_out=False)
    assert r.success is False


def test_cli_result_timeout_is_failure():
    r = CliResult(cmd=[], returncode=-1, stdout="", stderr="", duration=120.0, timed_out=True)
    assert r.success is False


def test_find_claude_uses_shutil_which():
    with patch("cc_plugin_manager.cli.shutil.which", return_value="/usr/bin/claude"):
        cli = ClaudeCli.discover()
        assert cli.executable == "/usr/bin/claude"


def test_find_claude_raises_when_missing():
    with patch("cc_plugin_manager.cli.shutil.which", return_value=None):
        with pytest.raises(CliNotFoundError):
            ClaudeCli.discover()


def _fake_run(returncode=0, stdout="", stderr=""):
    class R:
        pass

    r = R()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def test_list_plugins_parses_json(monkeypatch):
    payload = json.dumps(
        [
            {
                "name": "context7",
                "marketplace": "plugins-official",
                "scope": "user",
                "version": "1.0",
            },
            {"name": "github", "marketplace": None, "scope": "user"},
        ]
    )
    monkeypatch.setattr(
        "cc_plugin_manager.cli.subprocess.run",
        lambda *a, **kw: _fake_run(stdout=payload),
    )
    cli = ClaudeCli(executable="claude")
    plugins = cli.list_plugins()
    assert plugins == [
        InstalledPlugin(
            name="context7", marketplace="plugins-official", scope="user", version="1.0"
        ),
        InstalledPlugin(name="github", marketplace=None, scope="user", version=None),
    ]


def test_list_plugins_parses_real_cli_shape(monkeypatch):
    """The real `claude plugin list --json` returns an `id` field like
    `name@marketplace`, not separate `name`/`marketplace` keys."""
    payload = json.dumps(
        [
            {
                "id": "agent-sdk-dev@claude-plugins-official",
                "version": "unknown",
                "scope": "user",
                "enabled": True,
            },
            {
                "id": "loose-plugin",
                "version": "1.2.3",
                "scope": "user",
            },
        ]
    )
    monkeypatch.setattr(
        "cc_plugin_manager.cli.subprocess.run",
        lambda *a, **kw: _fake_run(stdout=payload),
    )
    cli = ClaudeCli(executable="claude")
    assert cli.list_plugins() == [
        InstalledPlugin(
            name="agent-sdk-dev",
            marketplace="claude-plugins-official",
            scope="user",
            version="unknown",
        ),
        InstalledPlugin(name="loose-plugin", marketplace=None, scope="user", version="1.2.3"),
    ]


def test_list_plugins_tolerates_unknown_fields(monkeypatch):
    payload = json.dumps([{"name": "x", "future_field": "ignored"}])
    monkeypatch.setattr(
        "cc_plugin_manager.cli.subprocess.run",
        lambda *a, **kw: _fake_run(stdout=payload),
    )
    cli = ClaudeCli(executable="claude")
    assert cli.list_plugins() == [
        InstalledPlugin(name="x", marketplace=None, scope=None, version=None)
    ]


def test_list_plugins_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        "cc_plugin_manager.cli.subprocess.run",
        lambda *a, **kw: _fake_run(returncode=1, stderr="boom"),
    )
    cli = ClaudeCli(executable="claude")
    assert cli.list_plugins() is None


def test_list_plugins_bad_json_returns_none(monkeypatch):
    monkeypatch.setattr(
        "cc_plugin_manager.cli.subprocess.run",
        lambda *a, **kw: _fake_run(stdout="not json"),
    )
    cli = ClaudeCli(executable="claude")
    assert cli.list_plugins() is None


def test_list_marketplaces_parses_json(monkeypatch):
    payload = json.dumps([{"name": "m1"}, {"name": "m2"}])
    monkeypatch.setattr(
        "cc_plugin_manager.cli.subprocess.run",
        lambda *a, **kw: _fake_run(stdout=payload),
    )
    cli = ClaudeCli(executable="claude")
    assert cli.list_marketplaces() == {"m1", "m2"}


def test_list_marketplaces_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        "cc_plugin_manager.cli.subprocess.run",
        lambda *a, **kw: _fake_run(returncode=1, stderr="err"),
    )
    cli = ClaudeCli(executable="claude")
    assert cli.list_marketplaces() is None


def test_list_plugins_uses_json_flag(monkeypatch):
    captured: dict = {}

    def fake(cmd, **kw):
        captured["cmd"] = cmd
        return _fake_run(stdout="[]")

    monkeypatch.setattr("cc_plugin_manager.cli.subprocess.run", fake)
    ClaudeCli(executable="claude").list_plugins()
    assert captured["cmd"] == ["claude", "plugin", "list", "--json"]


def test_list_marketplaces_uses_json_flag(monkeypatch):
    captured: dict = {}

    def fake(cmd, **kw):
        captured["cmd"] = cmd
        return _fake_run(stdout="[]")

    monkeypatch.setattr("cc_plugin_manager.cli.subprocess.run", fake)
    ClaudeCli(executable="claude").list_marketplaces()
    assert captured["cmd"] == ["claude", "plugin", "marketplace", "list", "--json"]


def _capture(monkeypatch):
    captured: dict = {}

    def fake(cmd, **kw):
        captured["cmd"] = cmd
        return _fake_run(returncode=0, stdout="ok")

    monkeypatch.setattr("cc_plugin_manager.cli.subprocess.run", fake)
    return captured


def test_install_builds_command(monkeypatch):
    captured = _capture(monkeypatch)
    cli = ClaudeCli(executable="claude")
    r = cli.install(Plugin("context7"), scope="user")
    assert r.success
    assert captured["cmd"] == ["claude", "plugin", "install", "context7", "--scope", "user"]


def test_install_includes_marketplace(monkeypatch):
    captured = _capture(monkeypatch)
    cli = ClaudeCli(executable="claude")
    cli.install(Plugin("session-report", "plugins-official"), scope="project")
    assert captured["cmd"] == [
        "claude",
        "plugin",
        "install",
        "session-report@plugins-official",
        "--scope",
        "project",
    ]


def test_update_builds_command(monkeypatch):
    captured = _capture(monkeypatch)
    cli = ClaudeCli(executable="claude")
    cli.update(Plugin("x"), scope="managed")
    assert captured["cmd"] == ["claude", "plugin", "update", "x", "--scope", "managed"]


def test_uninstall_builds_command(monkeypatch):
    captured = _capture(monkeypatch)
    cli = ClaudeCli(executable="claude")
    cli.uninstall(Plugin("x", "m"), scope="local")
    assert captured["cmd"] == ["claude", "plugin", "uninstall", "x@m", "--scope", "local"]


def test_add_marketplace_default_scope(monkeypatch):
    captured = _capture(monkeypatch)
    cli = ClaudeCli(executable="claude")
    cli.add_marketplace("affaan-m/everything--code")
    assert captured["cmd"] == [
        "claude",
        "plugin",
        "marketplace",
        "add",
        "affaan-m/everything--code",
        "--scope",
        "user",
    ]


def test_install_rejects_invalid_scope():
    cli = ClaudeCli(executable="claude")
    with pytest.raises(ValueError):
        cli.install(Plugin("x"), scope="bogus")


def test_update_accepts_managed_scope(monkeypatch):
    _capture(monkeypatch)
    cli = ClaudeCli(executable="claude")
    cli.update(Plugin("x"), scope="managed")


def test_install_rejects_managed_scope():
    cli = ClaudeCli(executable="claude")
    with pytest.raises(ValueError):
        cli.install(Plugin("x"), scope="managed")


def test_remove_marketplace_builds_command(monkeypatch):
    captured = _capture(monkeypatch)
    cli = ClaudeCli(executable="claude")
    cli.remove_marketplace("everything-claude-code")
    assert captured["cmd"] == [
        "claude",
        "plugin",
        "marketplace",
        "remove",
        "everything-claude-code",
    ]


def test_remove_marketplace_rejects_empty_name():
    cli = ClaudeCli(executable="claude")
    with pytest.raises(ValueError):
        cli.remove_marketplace("")
    with pytest.raises(ValueError):
        cli.remove_marketplace("   ")


def test_update_marketplace_with_name(monkeypatch):
    captured = _capture(monkeypatch)
    cli = ClaudeCli(executable="claude")
    cli.update_marketplace("superpowers-marketplace")
    assert captured["cmd"] == [
        "claude",
        "plugin",
        "marketplace",
        "update",
        "superpowers-marketplace",
    ]


def test_update_marketplace_without_name_updates_all(monkeypatch):
    captured = _capture(monkeypatch)
    cli = ClaudeCli(executable="claude")
    cli.update_marketplace()
    assert captured["cmd"] == ["claude", "plugin", "marketplace", "update"]


def test_update_marketplace_rejects_empty_string_name():
    cli = ClaudeCli(executable="claude")
    with pytest.raises(ValueError):
        cli.update_marketplace("   ")
