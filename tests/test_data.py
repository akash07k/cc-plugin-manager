import json
import os

import pytest
from cc_plugin_manager.data import Config, InstalledPlugin, Marketplace, Plugin, normalize_plugin_id


def test_plugin_is_hashable_and_equal():
    a = Plugin(name="context7", marketplace=None)
    b = Plugin(name="context7", marketplace=None)
    assert a == b
    assert hash(a) == hash(b)


def test_plugin_with_marketplace_differs():
    a = Plugin(name="x", marketplace=None)
    b = Plugin(name="x", marketplace="y")
    assert a != b


def test_marketplace_default_source_is_none():
    m = Marketplace(name="plugins-official")
    assert m.source is None
    assert m.is_auto_addable is False


def test_marketplace_with_source_is_addable():
    m = Marketplace(name="x", source="owner/repo")
    assert m.is_auto_addable is True


def test_config_exposes_marketplace_by_name():
    cfg = Config(
        marketplaces=[Marketplace(name="m1"), Marketplace(name="m2", source="o/r")],
        plugins=[],
    )
    assert cfg.marketplace_by_name("m2").source == "o/r"
    assert cfg.marketplace_by_name("missing") is None


def test_normalize_bare_string():
    assert normalize_plugin_id("context7") == Plugin(name="context7", marketplace=None)


def test_normalize_with_marketplace():
    assert normalize_plugin_id("session-report@plugins-official") == Plugin(
        name="session-report", marketplace="plugins-official"
    )


def test_normalize_strips_leading_dash():
    assert normalize_plugin_id("-context7") == Plugin(name="context7", marketplace=None)


def test_normalize_strips_whitespace():
    assert normalize_plugin_id("  context7  ") == Plugin(name="context7", marketplace=None)


def test_normalize_strips_leading_dashes_and_whitespace():
    assert normalize_plugin_id("  --context7@mkt  ") == Plugin(name="context7", marketplace="mkt")


def test_normalize_dict_form():
    assert normalize_plugin_id({"name": "x", "marketplace": "y"}) == Plugin("x", "y")


def test_normalize_dict_without_marketplace():
    assert normalize_plugin_id({"name": "x"}) == Plugin("x", None)


def test_normalize_rejects_empty():
    with pytest.raises(ValueError):
        normalize_plugin_id("")
    with pytest.raises(ValueError):
        normalize_plugin_id("   ")
    with pytest.raises(ValueError):
        normalize_plugin_id({"name": ""})


def test_normalize_rejects_multiple_at_signs():
    with pytest.raises(ValueError):
        normalize_plugin_id("x@y@z")


def test_normalize_rejects_wrong_type():
    with pytest.raises(TypeError):
        normalize_plugin_id(123)  # type: ignore[arg-type]


def test_load_config_basic(tmp_path):
    from cc_plugin_manager.data import load_config

    p = tmp_path / "plugins.json"
    p.write_text(
        json.dumps(
            {
                "marketplaces": [
                    {"name": "m1"},
                    {"name": "m2", "source": "o/r"},
                ],
                "plugins": ["a", {"name": "b", "marketplace": "m1"}],
            }
        )
    )
    cfg = load_config(str(p))
    assert len(cfg.marketplaces) == 2
    assert cfg.marketplace_by_name("m2").source == "o/r"
    assert cfg.plugins == [Plugin("a"), Plugin("b", "m1")]


def test_load_config_dedup_prefers_qualified(tmp_path):
    """Dedup rule: marketplace-qualified entry wins over bare duplicate."""
    from cc_plugin_manager.data import load_config

    p = tmp_path / "plugins.json"
    p.write_text(
        json.dumps(
            {
                "marketplaces": [{"name": "mkt"}],
                "plugins": [
                    "session-report",
                    {"name": "session-report", "marketplace": "mkt"},
                    "other",
                ],
            }
        )
    )
    cfg = load_config(str(p))
    names = [pl.qualified_id for pl in cfg.plugins]
    assert "session-report@mkt" in names
    assert "session-report" not in names
    assert "other" in names


def test_load_config_dedup_reverse_order(tmp_path):
    """Qualified entry still wins when it appears before the bare duplicate."""
    from cc_plugin_manager.data import load_config

    p = tmp_path / "plugins.json"
    p.write_text(
        json.dumps(
            {
                "marketplaces": [{"name": "mkt"}],
                "plugins": [
                    {"name": "x", "marketplace": "mkt"},
                    "x",
                ],
            }
        )
    )
    cfg = load_config(str(p))
    assert len(cfg.plugins) == 1
    assert cfg.plugins[0] == Plugin("x", "mkt")


def test_load_config_missing_file_raises(tmp_path):
    from cc_plugin_manager.data import load_config, ConfigError

    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "missing.json"))


def test_load_config_bad_json_raises(tmp_path):
    from cc_plugin_manager.data import load_config, ConfigError

    p = tmp_path / "plugins.json"
    p.write_text("not json {")
    with pytest.raises(ConfigError):
        load_config(str(p))


def test_load_config_missing_sections_raises(tmp_path):
    from cc_plugin_manager.data import load_config, ConfigError

    p = tmp_path / "plugins.json"
    p.write_text("{}")
    with pytest.raises(ConfigError):
        load_config(str(p))


def test_load_config_rejects_bad_plugin_entry(tmp_path):
    """Malformed individual entries raise ConfigError with a helpful message."""
    from cc_plugin_manager.data import load_config, ConfigError

    p = tmp_path / "plugins.json"
    p.write_text(
        json.dumps(
            {
                "marketplaces": [],
                "plugins": ["ok", ""],
            }
        )
    )
    with pytest.raises(ConfigError):
        load_config(str(p))


def test_load_config_rejects_undeclared_marketplace_ref(tmp_path):
    """Plugin entry referencing a marketplace not in the marketplaces list fails fast."""
    from cc_plugin_manager.data import load_config, ConfigError

    p = tmp_path / "plugins.json"
    p.write_text(
        json.dumps(
            {
                "marketplaces": [{"name": "declared"}],
                "plugins": [{"name": "x", "marketplace": "tpyo"}],
            }
        )
    )
    with pytest.raises(ConfigError, match="undeclared marketplaces"):
        load_config(str(p))


def test_load_config_accepts_declared_marketplace_ref(tmp_path):
    """Sanity: when the marketplace IS declared, validation passes."""
    from cc_plugin_manager.data import load_config

    p = tmp_path / "plugins.json"
    p.write_text(
        json.dumps(
            {
                "marketplaces": [{"name": "mkt", "source": "o/r"}],
                "plugins": [{"name": "x", "marketplace": "mkt"}],
            }
        )
    )
    cfg = load_config(str(p))
    assert cfg.plugins == [Plugin("x", "mkt")]


def test_load_config_rejects_non_string_marketplace_name(tmp_path):
    from cc_plugin_manager.data import load_config, ConfigError

    p = tmp_path / "plugins.json"
    p.write_text(
        json.dumps(
            {
                "marketplaces": [{"name": 42}],
                "plugins": [],
            }
        )
    )
    with pytest.raises(ConfigError, match="'name' must be a string"):
        load_config(str(p))


def test_load_config_rejects_non_string_marketplace_source(tmp_path):
    from cc_plugin_manager.data import load_config, ConfigError

    p = tmp_path / "plugins.json"
    p.write_text(
        json.dumps(
            {
                "marketplaces": [{"name": "m", "source": ["o", "r"]}],
                "plugins": [],
            }
        )
    )
    with pytest.raises(ConfigError, match="'source' must be"):
        load_config(str(p))


def test_normalize_dict_rejects_non_string_marketplace():
    """normalize_plugin_id refuses a non-string marketplace value in dict form."""
    with pytest.raises(ValueError, match="must be a string or null"):
        normalize_plugin_id({"name": "x", "marketplace": 42})


def test_normalize_dict_rejects_at_sign_in_name():
    """Dict form with '@' in name silently breaks status derivation; reject it."""
    with pytest.raises(ValueError, match="must not contain '@'"):
        normalize_plugin_id({"name": "foo@bar"})
    # Bare string form is the right way to express this.
    assert normalize_plugin_id("foo@bar") == Plugin(name="foo", marketplace="bar")


def test_write_config_round_trip(tmp_path):
    """Whatever we write, load_config reads back to an equivalent Config."""
    from cc_plugin_manager.data import load_config, write_config

    cfg = Config(
        marketplaces=[
            Marketplace(name="z-mkt", source="o/z"),
            Marketplace(name="a-mkt"),  # built-in (no source)
        ],
        plugins=[
            Plugin("zeta"),
            Plugin("alpha", "a-mkt"),
            Plugin("beta"),
        ],
    )
    p = tmp_path / "plugins.json"
    write_config(str(p), cfg)
    re = load_config(str(p))
    # Marketplaces alphabetized by name.
    assert [m.name for m in re.marketplaces] == ["a-mkt", "z-mkt"]
    assert re.marketplace_by_name("z-mkt").source == "o/z"
    # Plugins alphabetized by name; bare strings stay bare.
    assert [p.qualified_id for p in re.plugins] == ["alpha@a-mkt", "beta", "zeta"]


def test_write_config_uses_bare_string_for_unqualified(tmp_path):
    """Plugins without a marketplace serialize as a bare string."""
    from cc_plugin_manager.data import write_config

    cfg = Config(marketplaces=[], plugins=[Plugin("foo"), Plugin("bar", "m")])
    # Need to declare the marketplace too so load_config wouldn't choke on it
    # if we round-tripped — but for shape-only check, read raw JSON.
    p = tmp_path / "plugins.json"
    cfg2 = Config(
        marketplaces=[Marketplace(name="m")],
        plugins=cfg.plugins,
    )
    write_config(str(p), cfg2)
    raw = json.loads(p.read_text(encoding="utf-8"))
    # Output is alphabetized by plugin name: "bar" sorts before "foo".
    assert raw["plugins"] == [{"name": "bar", "marketplace": "m"}, "foo"]
    assert raw["marketplaces"] == [{"name": "m"}]


def test_write_config_omits_source_when_none(tmp_path):
    """Built-in marketplaces (no source) serialize without a 'source' key."""
    from cc_plugin_manager.data import write_config

    cfg = Config(marketplaces=[Marketplace(name="builtin")], plugins=[])
    p = tmp_path / "plugins.json"
    write_config(str(p), cfg)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["marketplaces"] == [{"name": "builtin"}]
    assert "source" not in raw["marketplaces"][0]


def test_write_config_emits_trailing_newline(tmp_path):
    """Trailing newline keeps editors / git diff happy."""
    from cc_plugin_manager.data import write_config

    p = tmp_path / "plugins.json"
    write_config(str(p), Config())
    assert p.read_text(encoding="utf-8").endswith("\n")


def test_write_config_is_atomic_on_failure(tmp_path, monkeypatch):
    """If serialisation fails mid-write, the target file is untouched.

    Simulates power-loss / AV-scan / Ctrl-C scenarios. Without the temp-file +
    os.replace pattern (C-3 audit fix), the user's plugins.json would be left
    truncated. With it, a partial write at most leaves a .tmp sibling.
    """
    from cc_plugin_manager.data import write_config

    p = tmp_path / "plugins.json"
    p.write_text('{"marketplaces": [], "plugins": ["original"]}\n', encoding="utf-8")
    original = p.read_text(encoding="utf-8")

    # Inject a failure inside the os.replace call. Anything raising mid-write
    # would do — pick os.replace because it's the moment of truth.
    real_replace = os.replace

    def boom(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("cc_plugin_manager.data.os.replace", boom)
    cfg = Config(marketplaces=[Marketplace(name="m")], plugins=[Plugin("new", "m")])
    with pytest.raises(OSError, match="simulated replace failure"):
        write_config(str(p), cfg)

    # Original content survives — the user is not stranded with a half-written
    # plugins.json.
    assert p.read_text(encoding="utf-8") == original
    # The temp file should also have been cleaned up.
    leftover = list(tmp_path.glob(".plugins-*.json.tmp"))
    assert leftover == [], f"temp files leaked: {leftover}"

    # Sanity: clearing the monkeypatch restores normal behaviour.
    monkeypatch.setattr("cc_plugin_manager.data.os.replace", real_replace)
    write_config(str(p), cfg)
    assert "new" in p.read_text(encoding="utf-8")


def test_status_marketplace_missing_undeclared():
    """Plugin references a marketplace not declared in plugins.json."""
    from cc_plugin_manager.data import derive_status, PluginStatus

    cfg = Config(marketplaces=[], plugins=[])
    plugin = Plugin(name="x", marketplace="undeclared")
    assert (
        derive_status(plugin, cfg, installed=[], present_markets=set())
        == PluginStatus.MARKETPLACE_MISSING
    )


def test_status_marketplace_missing_non_addable():
    """Declared marketplace with source=None, not present in CLI → missing."""
    from cc_plugin_manager.data import derive_status, PluginStatus

    cfg = Config(marketplaces=[Marketplace(name="m")], plugins=[])
    plugin = Plugin(name="x", marketplace="m")
    assert (
        derive_status(plugin, cfg, installed=[], present_markets=set())
        == PluginStatus.MARKETPLACE_MISSING
    )


def test_status_not_installed_addable_marketplace():
    from cc_plugin_manager.data import derive_status, PluginStatus

    cfg = Config(marketplaces=[Marketplace(name="m", source="o/r")], plugins=[])
    plugin = Plugin(name="x", marketplace="m")
    result = derive_status(plugin, cfg, installed=[], present_markets=set())
    assert result == PluginStatus.NOT_INSTALLED


def test_status_not_installed_present_non_addable_marketplace():
    """source=None but CLI reports marketplace present → treat as usable."""
    from cc_plugin_manager.data import derive_status, PluginStatus

    cfg = Config(marketplaces=[Marketplace(name="m")], plugins=[])
    plugin = Plugin(name="x", marketplace="m")
    result = derive_status(plugin, cfg, installed=[], present_markets={"m"})
    assert result == PluginStatus.NOT_INSTALLED


def test_status_installed():
    from cc_plugin_manager.data import derive_status, PluginStatus

    cfg = Config(marketplaces=[Marketplace(name="m", source="o/r")], plugins=[])
    plugin = Plugin(name="x", marketplace="m")
    installed = [InstalledPlugin(name="x", marketplace="m", scope="user", version="1.0")]
    result = derive_status(plugin, cfg, installed=installed, present_markets={"m"})
    assert result == PluginStatus.INSTALLED


def test_status_installed_matches_by_name_when_marketplace_unspecified():
    """Plugin entry has no marketplace; matches installed entry by name alone."""
    from cc_plugin_manager.data import derive_status, PluginStatus

    cfg = Config(marketplaces=[], plugins=[])
    plugin = Plugin(name="x", marketplace=None)
    installed = [InstalledPlugin(name="x", marketplace=None, scope="user", version=None)]
    assert (
        derive_status(plugin, cfg, installed=installed, present_markets=set())
        == PluginStatus.INSTALLED
    )


def test_status_unknown_when_signal_is_none():
    """Caller signals failure by passing installed=None."""
    from cc_plugin_manager.data import derive_status, PluginStatus

    cfg = Config(marketplaces=[Marketplace(name="m", source="o/r")], plugins=[])
    plugin = Plugin(name="x", marketplace="m")
    result = derive_status(plugin, cfg, installed=None, present_markets=set())
    assert result == PluginStatus.UNKNOWN
