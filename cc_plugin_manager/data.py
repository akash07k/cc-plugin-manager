"""Config parsing, models, and status derivation. No UI imports.

This module is a pure dataclass + parser layer. Everything below the UI relies
on it but it imports nothing project-internal — that property keeps the layer
trivially testable and makes mypy strict-mode tractable.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional


@dataclass(frozen=True)
class Plugin:
    name: str
    marketplace: Optional[str] = None

    @property
    def qualified_id(self) -> str:
        if self.marketplace:
            return f"{self.name}@{self.marketplace}"
        return self.name


@dataclass(frozen=True)
class Marketplace:
    name: str
    source: Optional[str] = None

    @property
    def is_auto_addable(self) -> bool:
        return self.source is not None and self.source.strip() != ""


@dataclass(frozen=True)
class InstalledPlugin:
    name: str
    marketplace: Optional[str]
    scope: Optional[str]
    version: Optional[str]


@dataclass
class Config:
    marketplaces: list[Marketplace] = field(default_factory=list)
    plugins: list[Plugin] = field(default_factory=list)

    def marketplace_by_name(self, name: str) -> Optional[Marketplace]:
        for m in self.marketplaces:
            if m.name == name:
                return m
        return None

    def marketplace_names(self) -> list[str]:
        return [m.name for m in self.marketplaces]


def normalize_plugin_id(raw: object) -> Plugin:
    """Accept a bare string or ``{name, marketplace?}`` dict and return a Plugin.

    Strips leading dashes and whitespace. Splits ``name@marketplace``.
    Raises ``ValueError`` on empty/malformed input, ``TypeError`` on wrong type.
    """
    if isinstance(raw, dict):
        raw_name = raw.get("name", "")
        if not isinstance(raw_name, str):
            raise ValueError(f"plugin entry 'name' must be a string, got {type(raw_name).__name__}")
        name = raw_name.strip()
        if not name:
            raise ValueError("plugin entry missing non-empty 'name'")
        # Reject "@" in dict-form name — otherwise the user can write
        # {"name": "foo@bar"} which round-trips as Plugin(name="foo@bar")
        # and the CLI parses install "foo@bar" as name=foo / marketplace=bar,
        # producing a silent NOT_INSTALLED state forever (audit M-4).
        if "@" in name:
            raise ValueError(
                f"plugin entry 'name' must not contain '@'; "
                f"use the bare-string form (e.g. {name!r}) or split into "
                "separate 'name' and 'marketplace' fields"
            )
        marketplace_obj = raw.get("marketplace")
        marketplace: Optional[str]
        if marketplace_obj is None:
            marketplace = None
        elif isinstance(marketplace_obj, str):
            marketplace = marketplace_obj.strip() or None
        else:
            raise ValueError(
                f"plugin entry 'marketplace' must be a string or null, "
                f"got {type(marketplace_obj).__name__}"
            )
        return Plugin(name=name, marketplace=marketplace)

    if isinstance(raw, str):
        stripped = raw.strip().lstrip("-").strip()
        if not stripped:
            raise ValueError("empty plugin identifier")
        if stripped.count("@") > 1:
            raise ValueError(f"invalid plugin id {raw!r}: multiple '@' separators")
        if "@" in stripped:
            name, marketplace = stripped.split("@", 1)
            name = name.strip()
            mkt = marketplace.strip() or None
            if not name:
                raise ValueError(f"invalid plugin id {raw!r}: empty name")
            return Plugin(name=name, marketplace=mkt)
        return Plugin(name=stripped, marketplace=None)

    raise TypeError(f"plugin entry must be str or dict, got {type(raw).__name__}")


class ConfigError(Exception):
    """Raised when plugins.json is missing, unreadable, or malformed."""


def write_config(path: str, config: Config) -> None:
    """Serialise ``config`` to ``path`` in canonical alphabetized form.

    Output format:

    - Marketplaces sorted by ``name``.
    - Plugins sorted by ``name``; bare strings for entries without a
      marketplace, ``{"name": ..., "marketplace": ...}`` objects otherwise.
    - 2-space indent, trailing newline.

    The result is round-trippable: ``load_config(write_config(p, c))`` recovers
    a config equal to ``c``. Any user-side hand-edits (formatting, ordering)
    are NOT preserved — this writer is for programmatic edits only.

    **Atomic write**: serialises to a sibling temp file then ``os.replace``s
    over the target. A crash, power loss, or AV scan mid-write cannot leave
    ``plugins.json`` truncated — either the new content lands wholesale or
    the old content survives unchanged. ``os.replace`` is atomic on both
    POSIX and Windows since Python 3.3.
    """
    marketplaces_out: list[dict[str, str]] = []
    for m in sorted(config.marketplaces, key=lambda m: m.name):
        entry: dict[str, str] = {"name": m.name}
        if m.source:
            entry["source"] = m.source
        marketplaces_out.append(entry)

    plugins_out: list[object] = []
    for p in sorted(config.plugins, key=lambda p: p.name):
        if p.marketplace:
            plugins_out.append({"name": p.name, "marketplace": p.marketplace})
        else:
            plugins_out.append(p.name)

    payload = {"marketplaces": marketplaces_out, "plugins": plugins_out}

    target = os.path.abspath(path)
    target_dir = os.path.dirname(target) or "."
    fd, tmp = tempfile.mkstemp(prefix=".plugins-", suffix=".json.tmp", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # fsync isn't supported on every filesystem (e.g., some
                # Windows network shares); the os.replace below is still
                # atomic, just without a durability guarantee.
                pass
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_config(path: str) -> Config:
    """Load and validate a ``plugins.json`` file.

    Validation rules (any failure raises :class:`ConfigError`):

    1. The file must exist and be valid UTF-8 JSON.
    2. Root must be a JSON object with ``marketplaces`` and ``plugins`` keys.
    3. Each marketplace must have a non-empty string ``name`` and a
       string-or-null ``source``. Duplicate marketplace names are rejected.
    4. Each plugin entry passes :func:`normalize_plugin_id`.
    5. Every plugin's ``marketplace`` (if set) must appear in the declared
       marketplaces list. This catches typos and stale references at startup
       instead of silently surfacing as ``MARKETPLACE_MISSING`` at runtime.

    After validation, the dedup rule applies: if both a bare ``name`` and a
    ``name@marketplace`` entry exist, the qualified entry wins.

    .. note::

        Validation is **structural only**. We can't verify that a marketplace
        ``name`` matches the upstream source's own ``marketplace.json:name`` —
        that would require a network call at load time. If the two differ, the
        CLI auto-adds under the upstream's canonical name, and our plugin
        entries (which reference our declared name) will surface as
        ``MARKETPLACE_MISSING`` forever. When adding a new marketplace, fetch
        ``<source>/.claude-plugin/marketplace.json`` and copy the ``name``
        field verbatim into ``plugins.json``.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"config file is not valid JSON: {e}") from e
    except OSError as e:
        raise ConfigError(f"could not read config file: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError("config root must be a JSON object")
    if "marketplaces" not in raw or "plugins" not in raw:
        raise ConfigError("config must contain 'marketplaces' and 'plugins' keys")

    marketplaces = _parse_marketplaces(raw["marketplaces"])
    plugins = _parse_and_dedup_plugins(raw["plugins"])
    _validate_marketplace_refs(plugins, marketplaces)
    return Config(marketplaces=marketplaces, plugins=plugins)


def _parse_marketplaces(raw: object) -> list[Marketplace]:
    if not isinstance(raw, list):
        raise ConfigError("'marketplaces' must be a list")
    out: list[Marketplace] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"marketplaces[{i}] must be an object")
        raw_name = entry.get("name")
        if not isinstance(raw_name, str):
            raise ConfigError(f"marketplaces[{i}] 'name' must be a string")
        name = raw_name.strip()
        if not name:
            raise ConfigError(f"marketplaces[{i}] missing non-empty 'name'")
        if name in seen:
            raise ConfigError(f"duplicate marketplace name: {name!r}")
        raw_source = entry.get("source")
        source: Optional[str]
        if raw_source is None:
            source = None
        elif isinstance(raw_source, str):
            source = raw_source.strip() or None
        else:
            raise ConfigError(f"marketplaces[{i}] 'source' must be a string or null")
        out.append(Marketplace(name=name, source=source))
        seen.add(name)
    return out


def _parse_and_dedup_plugins(raw: object) -> list[Plugin]:
    """Parse plugin entries and apply the dedup rule.

    Dedup rule: if both a bare ``name`` and a ``name@marketplace`` entry exist,
    the qualified entry wins. Preserves first-seen order of winners.
    """
    if not isinstance(raw, list):
        raise ConfigError("'plugins' must be a list")

    parsed: list[Plugin] = []
    for i, entry in enumerate(raw):
        try:
            parsed.append(normalize_plugin_id(entry))
        except (ValueError, TypeError) as e:
            raise ConfigError(f"plugins[{i}]: {e}") from e

    qualified_names = {p.name for p in parsed if p.marketplace is not None}
    out: list[Plugin] = []
    seen: set[tuple[str, Optional[str]]] = set()
    for p in parsed:
        if p.marketplace is None and p.name in qualified_names:
            continue  # drop bare duplicate
        key = (p.name, p.marketplace)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _validate_marketplace_refs(plugins: list[Plugin], marketplaces: list[Marketplace]) -> None:
    """Reject plugins referencing a marketplace not in the declared list."""
    declared = {m.name for m in marketplaces}
    bad: list[str] = []
    for p in plugins:
        if p.marketplace is not None and p.marketplace not in declared:
            bad.append(f"{p.qualified_id} (declared: {sorted(declared)})")
    if bad:
        raise ConfigError("plugin entries reference undeclared marketplaces: " + "; ".join(bad))


class PluginStatus(str, Enum):
    INSTALLED = "installed"
    NOT_INSTALLED = "not installed"
    MARKETPLACE_MISSING = "marketplace missing"
    UNKNOWN = "unknown"


def derive_status(
    plugin: Plugin,
    config: Config,
    installed: Optional[Iterable[InstalledPlugin]],
    present_markets: set[str],
) -> PluginStatus:
    """Return the current status of ``plugin`` given CLI readings.

    ``installed=None`` signals that the CLI query failed — status is
    :attr:`PluginStatus.UNKNOWN`.

    A plugin without an explicit marketplace is considered INSTALLED if **any**
    installed entry shares its name, regardless of which marketplace produced
    the install. This is intentional permissiveness — bare entries in
    ``plugins.json`` mean "any installation of name X counts" — and is covered
    by tests in :mod:`tests.test_data`.
    """
    if installed is None:
        return PluginStatus.UNKNOWN

    if plugin.marketplace is not None:
        declared = config.marketplace_by_name(plugin.marketplace)
        if declared is None:
            return PluginStatus.MARKETPLACE_MISSING
        if not declared.is_auto_addable and plugin.marketplace not in present_markets:
            return PluginStatus.MARKETPLACE_MISSING

    for ip in installed:
        if ip.name != plugin.name:
            continue
        if plugin.marketplace is None or ip.marketplace == plugin.marketplace:
            return PluginStatus.INSTALLED

    return PluginStatus.NOT_INSTALLED
