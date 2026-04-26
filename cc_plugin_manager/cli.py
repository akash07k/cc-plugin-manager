"""Wrapper over the ``claude`` CLI executable. No UI imports.

Timeouts are differentiated per action: list-style queries are bounded tightly
because they should complete in seconds; install/update/uninstall/marketplace-add
get a generous bound because they can pull from slow remote sources.

Override the defaults at construction time (e.g., for tests) or via the
``CC_PLUGIN_MANAGER_TIMEOUT_*`` environment variables on the user's side.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from .data import InstalledPlugin, Plugin


def _parse_installed_id(entry: dict[str, object]) -> tuple[Optional[str], Optional[str]]:
    """Extract (name, marketplace) from a ``claude plugin list --json`` entry.

    The real CLI returns entries like
    ``{"id": "agent-sdk-dev@claude-plugins-official", ...}``. The older test
    fixture shape used a separate ``name``/``marketplace`` pair; accept both
    so legacy fixtures keep working.
    """
    raw_id = entry.get("id")
    if isinstance(raw_id, str) and raw_id:
        name, sep, market = raw_id.partition("@")
        name = name.strip()
        marketplace: Optional[str] = market.strip() if sep else None
        if marketplace == "":
            marketplace = None
        return (name or None, marketplace)

    legacy_name = entry.get("name")
    if isinstance(legacy_name, str) and legacy_name:
        legacy_market = entry.get("marketplace")
        if not isinstance(legacy_market, str) or not legacy_market:
            legacy_market = None
        return (legacy_name, legacy_market)

    return (None, None)


class CliError(Exception):
    """Base error for CLI integration."""


class CliNotFoundError(CliError):
    """Raised when the ``claude`` executable is not on PATH."""


@dataclass(frozen=True)
class CliResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class Timeouts:
    """Per-action subprocess timeouts in seconds.

    Defaults are sized for the common case: list queries are fast (<5 s
    typical), install/update can pull tens of MB from a git remote, and
    marketplace-add does a clone-equivalent. They're overridable for tests
    and for users with unusually slow network conditions.

    Users can override individual timeouts via environment variables (read
    by :meth:`from_env`):

    - ``CC_PLUGIN_MANAGER_TIMEOUT_LIST``
    - ``CC_PLUGIN_MANAGER_TIMEOUT_INSTALL``
    - ``CC_PLUGIN_MANAGER_TIMEOUT_UPDATE``
    - ``CC_PLUGIN_MANAGER_TIMEOUT_UNINSTALL``
    - ``CC_PLUGIN_MANAGER_TIMEOUT_MARKETPLACE``

    Each takes a non-negative float (seconds). Invalid values are silently
    ignored (the default for that field is kept) so a typo in one env var
    never bricks the app.
    """

    list_query: float = 30.0
    install: float = 600.0
    update: float = 600.0
    uninstall: float = 120.0
    marketplace_add: float = 300.0

    @classmethod
    def from_env(cls) -> "Timeouts":
        """Build a Timeouts honoring ``CC_PLUGIN_MANAGER_TIMEOUT_*`` env vars."""
        defaults = cls()

        def _read(name: str, default: float) -> float:
            raw = os.environ.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                value = float(raw)
            except ValueError:
                return default
            return value if value > 0 else default

        return cls(
            list_query=_read("CC_PLUGIN_MANAGER_TIMEOUT_LIST", defaults.list_query),
            install=_read("CC_PLUGIN_MANAGER_TIMEOUT_INSTALL", defaults.install),
            update=_read("CC_PLUGIN_MANAGER_TIMEOUT_UPDATE", defaults.update),
            uninstall=_read("CC_PLUGIN_MANAGER_TIMEOUT_UNINSTALL", defaults.uninstall),
            marketplace_add=_read(
                "CC_PLUGIN_MANAGER_TIMEOUT_MARKETPLACE", defaults.marketplace_add
            ),
        )


@dataclass
class ClaudeCli:
    executable: str
    timeouts: Timeouts = field(default_factory=Timeouts)

    SCOPES_INSTALL = ("user", "project", "local")
    SCOPES_UPDATE = ("user", "project", "local", "managed")
    SCOPES_UNINSTALL = ("user", "project", "local")
    SCOPES_MARKETPLACE_ADD = ("user", "project", "local")

    @classmethod
    def discover(cls, timeouts: Optional[Timeouts] = None) -> "ClaudeCli":
        found = shutil.which("claude")
        if found is None:
            raise CliNotFoundError(
                "The 'claude' CLI was not found on PATH. Install Claude Code and restart."
            )
        # When the caller doesn't override, honor the CC_PLUGIN_MANAGER_TIMEOUT_*
        # env vars (audit I-4).
        return cls(executable=found, timeouts=timeouts or Timeouts.from_env())

    def _run(self, args: list[str], timeout: float) -> CliResult:
        cmd = [self.executable, *args]
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            return CliResult(
                cmd=cmd,
                returncode=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration=time.monotonic() - started,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as e:
            stdout = self._decode_partial(e.stdout)
            stderr = self._decode_partial(e.stderr)
            return CliResult(
                cmd=cmd,
                returncode=-1,
                stdout=stdout,
                stderr=stderr,
                duration=time.monotonic() - started,
                timed_out=True,
            )

    @staticmethod
    def _decode_partial(buf: object) -> str:
        if buf is None:
            return ""
        if isinstance(buf, bytes):
            return buf.decode("utf-8", errors="replace")
        if isinstance(buf, str):
            return buf
        return str(buf)

    def list_plugins(self) -> Optional[list[InstalledPlugin]]:
        """Return installed plugins, or ``None`` if the CLI call failed."""
        result = self._run(["plugin", "list", "--json"], timeout=self.timeouts.list_query)
        if not result.success:
            return None
        try:
            raw = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(raw, list):
            return None
        out: list[InstalledPlugin] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name, marketplace = _parse_installed_id(entry)
            if not name:
                continue
            scope_obj = entry.get("scope")
            scope: Optional[str] = scope_obj if isinstance(scope_obj, str) else None
            version_obj = entry.get("version")
            version: Optional[str] = version_obj if isinstance(version_obj, str) else None
            out.append(
                InstalledPlugin(name=name, marketplace=marketplace, scope=scope, version=version)
            )
        return out

    def list_marketplaces(self) -> Optional[set[str]]:
        """Return the set of marketplace names known to the CLI, or None on failure."""
        result = self._run(
            ["plugin", "marketplace", "list", "--json"],
            timeout=self.timeouts.list_query,
        )
        if not result.success:
            return None
        try:
            raw = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(raw, list):
            return None
        names: set[str] = set()
        for entry in raw:
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str) and name:
                    names.add(name)
            elif isinstance(entry, str) and entry:
                names.add(entry)
        return names

    def install(self, plugin: Plugin, scope: str) -> CliResult:
        if scope not in self.SCOPES_INSTALL:
            raise ValueError(f"invalid install scope: {scope!r}")
        return self._run(
            ["plugin", "install", plugin.qualified_id, "--scope", scope],
            timeout=self.timeouts.install,
        )

    def update(self, plugin: Plugin, scope: str) -> CliResult:
        if scope not in self.SCOPES_UPDATE:
            raise ValueError(f"invalid update scope: {scope!r}")
        return self._run(
            ["plugin", "update", plugin.qualified_id, "--scope", scope],
            timeout=self.timeouts.update,
        )

    def uninstall(self, plugin: Plugin, scope: str) -> CliResult:
        if scope not in self.SCOPES_UNINSTALL:
            raise ValueError(f"invalid uninstall scope: {scope!r}")
        return self._run(
            ["plugin", "uninstall", plugin.qualified_id, "--scope", scope],
            timeout=self.timeouts.uninstall,
        )

    def add_marketplace(self, source: str, scope: str = "user") -> CliResult:
        if scope not in self.SCOPES_MARKETPLACE_ADD:
            raise ValueError(f"invalid marketplace scope: {scope!r}")
        if not source or not source.strip():
            raise ValueError("marketplace source must be non-empty")
        return self._run(
            ["plugin", "marketplace", "add", source, "--scope", scope],
            timeout=self.timeouts.marketplace_add,
        )

    def remove_marketplace(self, name: str) -> CliResult:
        """Remove a configured marketplace by name.

        The CLI's ``marketplace remove`` does not accept ``--scope``; it
        operates on whichever scope holds the named marketplace.
        """
        if not name or not name.strip():
            raise ValueError("marketplace name must be non-empty")
        return self._run(
            ["plugin", "marketplace", "remove", name],
            timeout=self.timeouts.marketplace_add,
        )

    def update_marketplace(self, name: Optional[str] = None) -> CliResult:
        """Update one marketplace by name, or all when ``name`` is ``None``.

        Updating refreshes a marketplace from its source (git pull / re-fetch).
        Uses the ``marketplace_add`` timeout because the operation is similarly
        network-bound.
        """
        args = ["plugin", "marketplace", "update"]
        if name is not None:
            stripped = name.strip()
            if not stripped:
                raise ValueError("marketplace name must be non-empty when provided")
            args.append(stripped)
        return self._run(args, timeout=self.timeouts.marketplace_add)
