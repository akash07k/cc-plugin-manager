"""Background execution worker and event types. No UI imports.

The worker runs a queue of :class:`Operation` items sequentially on a
background thread. Events are posted through a single ``post_event`` callback;
the UI layer is responsible for marshalling the call onto the main thread
(typically via ``wx.CallAfter``).

Robustness rules:

- Each ``post_event`` call is guarded — a failure in event delivery (e.g.
  the destination frame was destroyed mid-run) must not crash the worker.
- The outer ``try/except`` only protects against bugs in the worker itself;
  ``_dispatch`` failures are caught individually and reported as
  :attr:`OpStatus.FAIL` so the run continues.
- Cancellation is cooperative — when ``cancel()`` is called, the worker
  finishes its current operation and posts the appropriate result, then
  breaks out of the loop. ``cancelled=True`` is reported on the
  :class:`RunCompleteEvent`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable, Iterable, Optional, Union

from .cli import ClaudeCli, CliResult
from .data import Plugin

if TYPE_CHECKING:
    from .data import Config, PluginStatus


class ActionKind(str, Enum):
    INSTALL = "install"
    UPDATE = "update"
    UNINSTALL = "uninstall"


class OpStatus(str, Enum):
    OK = "OK"
    FAIL = "FAIL"
    SKIP = "SKIP"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class MarketplaceAddOp:
    name: str
    source: str
    scope: str = "user"

    @property
    def label(self) -> str:
        return f"Add marketplace {self.name}"


@dataclass(frozen=True)
class PluginOp:
    action: ActionKind
    plugin: Plugin
    scope: str

    @property
    def label(self) -> str:
        verb = {
            ActionKind.INSTALL: "Installing",
            ActionKind.UPDATE: "Updating",
            ActionKind.UNINSTALL: "Uninstalling",
        }[self.action]
        return f"{verb} {self.plugin.qualified_id}"


@dataclass(frozen=True)
class SkipOp:
    plugin: Plugin
    reason: str

    @property
    def label(self) -> str:
        return f"Skip {self.plugin.qualified_id}"


@dataclass(frozen=True)
class MarketplaceRemoveOp:
    """Remove a registered marketplace by name."""

    name: str

    @property
    def label(self) -> str:
        return f"Remove marketplace {self.name}"


@dataclass(frozen=True)
class MarketplaceUpdateOp:
    """Update one marketplace, or all when ``name`` is ``None``."""

    name: Optional[str]

    @property
    def label(self) -> str:
        if self.name is None:
            return "Update all marketplaces"
        return f"Update marketplace {self.name}"


Operation = Union[
    MarketplaceAddOp,
    MarketplaceRemoveOp,
    MarketplaceUpdateOp,
    PluginOp,
    SkipOp,
]


@dataclass(frozen=True)
class ProgressEvent:
    index: int  # 1-based position of the current op
    total: int
    op: Operation


@dataclass(frozen=True)
class OpResultEvent:
    op: Operation
    status: OpStatus
    stdout: str
    stderr: str
    duration: float
    cmd: Optional[list[str]]


@dataclass(frozen=True)
class RunCompleteEvent:
    succeeded: int
    skipped: int
    failed: int
    cancelled: bool
    error: Optional[str] = None

    @property
    def total(self) -> int:
        return self.succeeded + self.skipped + self.failed


def build_operations(
    *,
    action: ActionKind,
    scope: str,
    selected: Iterable[tuple[Plugin, "PluginStatus"]],
    config: "Config",
    present_markets: set[str],
) -> list[Operation]:
    """Translate a user selection into an ordered list of operations.

    Rules:
    - Plugins with status MARKETPLACE_MISSING become SkipOps.
    - For each remaining plugin whose marketplace is declared, addable,
      and not yet present in ``present_markets``, emit one MarketplaceAddOp
      (deduplicated) before the PluginOps that reference it.
    - Selection order is preserved among PluginOps of the same marketplace.
    """
    from .data import PluginStatus  # local import to avoid circular typing

    ops: list[Operation] = []
    added: set[str] = set()

    for plugin, status in selected:
        if status == PluginStatus.MARKETPLACE_MISSING:
            reason = _skip_reason(plugin, config, present_markets)
            ops.append(SkipOp(plugin=plugin, reason=reason))
            continue

        if plugin.marketplace is not None and plugin.marketplace not in present_markets:
            declared = config.marketplace_by_name(plugin.marketplace)
            if declared is not None and declared.is_auto_addable and declared.name not in added:
                assert declared.source is not None
                ops.append(MarketplaceAddOp(name=declared.name, source=declared.source))
                added.add(declared.name)

        ops.append(PluginOp(action=action, plugin=plugin, scope=scope))

    return ops


def _skip_reason(plugin: Plugin, config: "Config", present_markets: set[str]) -> str:
    if plugin.marketplace is None:
        return f"{plugin.name}: unknown error"
    declared = config.marketplace_by_name(plugin.marketplace)
    if declared is None:
        return f"marketplace {plugin.marketplace!r} is not declared in plugins.json"
    if not declared.is_auto_addable and plugin.marketplace not in present_markets:
        return f"marketplace {plugin.marketplace!r} not registered and has no known source"
    return f"marketplace {plugin.marketplace!r} unavailable"


def cmd_for(op: Operation, executable: str) -> list[str]:
    """Build the equivalent ``claude`` command line for ``op``.

    Used for log lines and for surfacing the executed command to users.
    Public (no underscore) because the UI layer needs it.
    """
    if isinstance(op, MarketplaceAddOp):
        return [executable, "plugin", "marketplace", "add", op.source, "--scope", op.scope]
    if isinstance(op, MarketplaceRemoveOp):
        return [executable, "plugin", "marketplace", "remove", op.name]
    if isinstance(op, MarketplaceUpdateOp):
        cmd = [executable, "plugin", "marketplace", "update"]
        if op.name is not None:
            cmd.append(op.name)
        return cmd
    if isinstance(op, PluginOp):
        return [
            executable,
            "plugin",
            op.action.value,
            op.plugin.qualified_id,
            "--scope",
            op.scope,
        ]
    return [executable]


class ExecutionWorker(threading.Thread):
    """Runs a queue of Operations sequentially, posting events via ``post_event``.

    ``post_event`` is called from the worker thread. UI code must marshal to
    the main thread (e.g., ``wx.CallAfter``) inside the callback.
    """

    def __init__(
        self,
        *,
        cli: ClaudeCli,
        ops: list[Operation],
        post_event: Callable[[object], None],
    ) -> None:
        super().__init__(daemon=True)
        self._cli = cli
        self._ops = list(ops)
        self._post_raw = post_event
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def _post(self, evt: object) -> None:
        """Best-effort event delivery; never raise out of the worker.

        If the destination has gone away (e.g., main frame was destroyed),
        ``wx.CallAfter`` may raise ``RuntimeError`` from the callback chain.
        Swallow it — the worker still needs to finish gracefully so the
        thread exits and the process can clean up.
        """
        try:
            self._post_raw(evt)
        except Exception:
            pass

    def run(self) -> None:
        succeeded = 0
        failed = 0
        skipped = 0
        error: Optional[str] = None
        total = len(self._ops)

        try:
            for index, op in enumerate(self._ops, start=1):
                self._post(ProgressEvent(index=index, total=total, op=op))

                if isinstance(op, SkipOp):
                    self._post(
                        OpResultEvent(
                            op=op,
                            status=OpStatus.SKIP,
                            stdout="",
                            stderr=op.reason,
                            duration=0.0,
                            cmd=None,
                        )
                    )
                    skipped += 1
                    if self._cancel.is_set():
                        break
                    continue

                try:
                    result = _dispatch(self._cli, op)
                except Exception as e:  # noqa: BLE001 — worker must not crash
                    self._post(
                        OpResultEvent(
                            op=op,
                            status=OpStatus.FAIL,
                            stdout="",
                            stderr=str(e),
                            duration=0.0,
                            cmd=cmd_for(op, self._cli.executable),
                        )
                    )
                    failed += 1
                    if self._cancel.is_set():
                        break
                    continue

                if result.timed_out:
                    status = OpStatus.TIMEOUT
                    failed += 1
                elif result.success:
                    status = OpStatus.OK
                    succeeded += 1
                else:
                    status = OpStatus.FAIL
                    failed += 1

                self._post(
                    OpResultEvent(
                        op=op,
                        status=status,
                        stdout=result.stdout,
                        stderr=result.stderr,
                        duration=result.duration,
                        cmd=result.cmd,
                    )
                )

                if self._cancel.is_set():
                    break

        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"

        self._post(
            RunCompleteEvent(
                succeeded=succeeded,
                skipped=skipped,
                failed=failed,
                cancelled=self._cancel.is_set(),
                error=error,
            )
        )


def _dispatch(cli: ClaudeCli, op: Operation) -> CliResult:
    if isinstance(op, MarketplaceAddOp):
        return cli.add_marketplace(op.source, scope=op.scope)
    if isinstance(op, MarketplaceRemoveOp):
        return cli.remove_marketplace(op.name)
    if isinstance(op, MarketplaceUpdateOp):
        return cli.update_marketplace(op.name)
    if isinstance(op, PluginOp):
        if op.action == ActionKind.INSTALL:
            return cli.install(op.plugin, scope=op.scope)
        if op.action == ActionKind.UPDATE:
            return cli.update(op.plugin, scope=op.scope)
        if op.action == ActionKind.UNINSTALL:
            return cli.uninstall(op.plugin, scope=op.scope)
    raise TypeError(f"cannot dispatch {type(op).__name__}")
