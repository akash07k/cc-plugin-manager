"""Top-level wx.Frame for the Claude Code Plugin Manager.

Threading and accessibility rules baked in here:

- ``ExecutionWorker`` runs on a background daemon thread and posts events via
  ``wx.CallAfter`` to ``_on_worker_event``. The worker swallows post-time
  exceptions so a destroyed frame can't crash it (see worker.py).
- On close-during-run, ``_on_close`` requests cancellation and waits up to
  ``_CLOSE_WAIT_SECS`` for the worker to exit. Without this wait, ``Destroy``
  could fire while the worker still has ``CallAfter`` calls in flight,
  producing intermittent "wrapped C/C++ object has been deleted" errors.
- Live-region announcements during a run are **milestone-only** (start, every
  Nth op, every failure, completion). Per-op success announcements would
  flood NVDA's speech queue — the 200 ms debounce in ``LiveRegion`` replaces
  pending text, it does not aggregate. Per-op detail still goes to the log
  pane for users who want it.
- ``_set_running_state`` parks focus on Cancel before disabling other
  controls, so a screen-reader user never lands on a disabled widget.
- ``_finish_run`` shows the summary dialog **first**, then sets focus on
  Execute, then schedules the post-run refresh — that ordering keeps focus
  predictable and prevents three overlapping announcements.
- Concurrent refreshes (user mashing Refresh, or post-run refresh racing a
  manual one) are coalesced via a generation counter; only the latest
  in-flight refresh's result is applied.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Optional

import wx

from ..cli import ClaudeCli
from ..data import (
    Config,
    ConfigError,
    InstalledPlugin,
    Plugin,
    PluginStatus,
    derive_status,
    load_config,
)
from ..worker import (
    ActionKind,
    ExecutionWorker,
    MarketplaceAddOp,
    MarketplaceRemoveOp,
    MarketplaceUpdateOp,
    Operation,
    OpResultEvent,
    OpStatus,
    PluginOp,
    ProgressEvent,
    RunCompleteEvent,
    SkipOp,
    build_operations,
    cmd_for,
)
from .live_region import LiveRegion
from .log_pane import LogPane
from .plugin_list import PluginListCtrl


SCOPES_BY_ACTION: dict[ActionKind, tuple[str, ...]] = {
    ActionKind.INSTALL: ("user", "project", "local"),
    ActionKind.UPDATE: ("user", "project", "local", "managed"),
    ActionKind.UNINSTALL: ("user", "project", "local"),
}

# "All" first so the default selection is no-filter. The remaining choices
# mirror the PluginStatus enum's display values exactly so the dropdown text
# matches the Status column text in the list.
_STATUS_FILTER_ALL = "All"
_STATUS_FILTER_CHOICES: list[str] = [
    _STATUS_FILTER_ALL,
    PluginStatus.INSTALLED.value,
    PluginStatus.NOT_INSTALLED.value,
    PluginStatus.MARKETPLACE_MISSING.value,
    PluginStatus.UNKNOWN.value,
]


# Announce every Nth in-progress op to keep the live region informative
# without flooding the speech queue.
_PROGRESS_STRIDE = 5

# Force a milestone announcement whenever this many seconds have elapsed
# without one, even if the stride hasn't been hit. Prevents long destructive
# bulk ops (like uninstalling 30 plugins) from going silent for minutes — a
# screen-reader user reads silence as "the app froze". (a11y review C-2)
_ANNOUNCE_FALLBACK_SECS = 8.0

# Max seconds to wait for the worker to exit when the user closes the frame
# during a run.
_CLOSE_WAIT_SECS = 2.0


def _result_label(status: OpStatus) -> str:
    return {
        OpStatus.OK: "OK",
        OpStatus.FAIL: "FAIL",
        OpStatus.SKIP: "SKIP",
        OpStatus.TIMEOUT: "TIMEOUT",
    }[status]


class MainFrame(wx.Frame):
    def __init__(self, *, config: Config, cli: ClaudeCli) -> None:
        super().__init__(
            None,
            title="Claude Code Plugin Manager",
            size=(900, 650),
        )
        self.SetMinSize((700, 500))
        self._config = config
        self._cli = cli
        self._present_markets: set[str] = set()
        self._installed: Optional[list[InstalledPlugin]] = None
        self._worker: Optional[ExecutionWorker] = None
        self._closing = False
        self._refresh_gen = 0
        self._refresh_in_flight = False
        # Per-kind tallies for the active run (reset in _start_run). Used by
        # _finish_run to produce a clearer summary for mixed-kind runs like
        # "Reset everything" (a11y review C-1).
        self._run_label: str = ""
        self._run_plugin_ok = 0
        self._run_plugin_fail = 0
        self._run_plugin_skip = 0
        self._run_marketplace_ok = 0
        self._run_marketplace_fail = 0
        # Used by _on_progress_event to force a milestone announcement when
        # too much time passes without one (a11y review C-2).
        self._last_announce_monotonic = 0.0

        self._build_menu()
        self._build_widgets()
        self._build_layout()
        self._bind_events()
        self._populate_plugins_initial()

        # Refresh once on first show.
        self.Bind(wx.EVT_SHOW, self._on_first_show)

    # ------------------------------------------------------------------ UI

    def _build_menu(self) -> None:
        menubar = wx.MenuBar()

        file_menu = wx.Menu()
        self._menu_reload = file_menu.Append(wx.ID_ANY, "&Reload plugins.json")
        self._menu_add_plugin = file_menu.Append(wx.ID_ANY, "&Add plugin...")
        self._menu_marketplaces = file_menu.Append(wx.ID_ANY, "&Marketplaces...\tCtrl+M")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "E&xit\tAlt+F4")
        menubar.Append(file_menu, "&File")

        # Advanced (destructive bulk operations). Each item shows a
        # confirmation dialog stating the count + scope before doing anything.
        # All flow through ExecutionWorker so progress / cancel / log work
        # uniformly with the regular Execute path.
        adv_menu = wx.Menu()
        self._menu_update_all_plugins = adv_menu.Append(wx.ID_ANY, "&Update all installed plugins")
        self._menu_uninstall_all_plugins = adv_menu.Append(
            wx.ID_ANY, "U&ninstall all installed plugins..."
        )
        adv_menu.AppendSeparator()
        self._menu_update_all_marketplaces = adv_menu.Append(wx.ID_ANY, "Update all m&arketplaces")
        self._menu_remove_all_marketplaces = adv_menu.Append(
            wx.ID_ANY, "&Remove all marketplaces..."
        )
        adv_menu.AppendSeparator()
        self._menu_reset_everything = adv_menu.Append(wx.ID_ANY, "Reset &everything...")
        menubar.Append(adv_menu, "Ad&vanced")

        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, "&About")
        menubar.Append(help_menu, "&Help")

        self.SetMenuBar(menubar)

    def _build_widgets(self) -> None:
        panel = wx.Panel(self)
        self._panel = panel

        # Filters group
        self._filters_box = wx.StaticBox(panel, label="Filters")
        self._filter_label = wx.StaticText(panel, label="Filter by &Marketplace")
        self._filter_choice = wx.Choice(panel, choices=self._filter_choices())
        self._filter_choice.SetSelection(0)
        self._filter_choice.SetName("Filter by Marketplace")

        # Status filter (Alt+T). Composes (AND) with the marketplace filter,
        # so a user can quickly narrow to e.g. "everything-claude-code, not
        # installed" without scanning the whole list.
        self._status_filter_label = wx.StaticText(panel, label="Filter by Sta&tus")
        self._status_filter_choice = wx.Choice(panel, choices=_STATUS_FILTER_CHOICES)
        self._status_filter_choice.SetSelection(0)
        self._status_filter_choice.SetName("Filter by Status")

        self._action_radio = wx.RadioBox(
            panel,
            label="&Action",
            choices=["Install", "Update", "Uninstall"],
            majorDimension=3,
            style=wx.RA_SPECIFY_COLS,
        )

        self._scope_label = wx.StaticText(panel, label="&Scope")
        self._scope_choice = wx.Choice(panel, choices=list(SCOPES_BY_ACTION[ActionKind.INSTALL]))
        self._scope_choice.SetSelection(0)
        self._scope_choice.SetName("Scope")

        # Plugin list
        self._plugin_label = wx.StaticText(panel, label="&Plugins")
        self._plugin_list = PluginListCtrl(panel)

        # Selection buttons
        self._btn_select = wx.Button(panel, label="Se&lect All")
        self._btn_deselect = wx.Button(panel, label="&Deselect All")
        self._btn_refresh = wx.Button(panel, label="&Refresh")

        # Execute/Cancel
        self._btn_execute = wx.Button(panel, label="E&xecute")
        self._btn_execute.SetDefault()
        self._btn_cancel = wx.Button(panel, label="&Cancel")
        self._btn_cancel.Disable()

        # Progress
        self._gauge = wx.Gauge(panel, range=1, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        self._gauge.SetName("Progress")
        self._live = LiveRegion(panel, label="Ready")

        # Log — Alt+G (was Alt+L, which collided with "Se&lect All")
        self._log_label = wx.StaticText(panel, label="Lo&g")
        self._log = LogPane(panel)

        # Status bar: field 0 carries status/progress messages; field 1 is a
        # persistent "N selected" counter that screen-reader users can read
        # on demand (NVDA+End) without re-scanning the list.
        self.CreateStatusBar(2)
        self.SetStatusWidths([-1, 160])
        self.SetStatusText("Ready", 0)
        self.SetStatusText("0 selected", 1)

    def _build_layout(self) -> None:
        panel = self._panel

        filters_sizer = wx.StaticBoxSizer(self._filters_box, wx.VERTICAL)

        # Filter row: StaticText immediately precedes Choice for UIA LabeledBy.
        filter_row = wx.BoxSizer(wx.HORIZONTAL)
        filter_row.Add(self._filter_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        filter_row.Add(self._filter_choice, 1, wx.EXPAND)
        filters_sizer.Add(filter_row, 0, wx.EXPAND | wx.ALL, 6)

        # Status filter on its own row, same StaticText-then-Choice pattern.
        status_filter_row = wx.BoxSizer(wx.HORIZONTAL)
        status_filter_row.Add(self._status_filter_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        status_filter_row.Add(self._status_filter_choice, 1, wx.EXPAND)
        filters_sizer.Add(status_filter_row, 0, wx.EXPAND | wx.ALL, 6)

        # Action on its own row.
        filters_sizer.Add(self._action_radio, 0, wx.EXPAND | wx.ALL, 6)

        # Scope on its own row so the StaticText immediately precedes the
        # Choice without a RadioBox between them — this matters for the UIA
        # LabeledBy association on Windows.
        scope_row = wx.BoxSizer(wx.HORIZONTAL)
        scope_row.Add(self._scope_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        scope_row.Add(self._scope_choice, 0, wx.ALIGN_CENTER_VERTICAL)
        filters_sizer.Add(scope_row, 0, wx.EXPAND | wx.ALL, 6)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.Add(self._btn_select, 0, wx.RIGHT, 6)
        btn_row.Add(self._btn_deselect, 0, wx.RIGHT, 6)
        btn_row.Add(self._btn_refresh, 0)

        exec_row = wx.BoxSizer(wx.HORIZONTAL)
        exec_row.Add(self._btn_execute, 0, wx.RIGHT, 6)
        exec_row.Add(self._btn_cancel, 0)

        progress_row = wx.BoxSizer(wx.HORIZONTAL)
        progress_row.Add(self._gauge, 1, wx.EXPAND | wx.RIGHT, 6)
        progress_row.Add(self._live, 2, wx.ALIGN_CENTER_VERTICAL)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(filters_sizer, 0, wx.EXPAND | wx.ALL, 6)
        outer.Add(self._plugin_label, 0, wx.LEFT | wx.TOP, 6)
        outer.Add(self._plugin_list, 3, wx.EXPAND | wx.ALL, 6)
        outer.Add(btn_row, 0, wx.EXPAND | wx.ALL, 6)
        outer.Add(exec_row, 0, wx.EXPAND | wx.ALL, 6)
        outer.Add(progress_row, 0, wx.EXPAND | wx.ALL, 6)
        outer.Add(self._log_label, 0, wx.LEFT | wx.TOP, 6)
        outer.Add(self._log, 2, wx.EXPAND | wx.ALL, 6)

        panel.SetSizer(outer)

    def _filter_choices(self) -> list[str]:
        return ["All"] + self._config.marketplace_names()

    def _populate_plugins_initial(self, *, preserve_selection: bool = False) -> None:
        """Populate with 'unknown' status until first refresh completes.

        ``preserve_selection`` defaults to False (initial construction has no
        prior selection to preserve), but Reload sets it True so the user's
        carefully-chosen subset survives a config edit (audit M-10).
        """
        plugins = list(self._config.plugins)
        statuses = [PluginStatus.UNKNOWN] * len(plugins)
        filtered_plugins, filtered_statuses = self._filter_pair(plugins, statuses)
        self._plugin_list.set_rows(
            filtered_plugins, filtered_statuses, preserve_selection=preserve_selection
        )
        self._update_selection_status()

    def _filter_pair(
        self, plugins: list[Plugin], statuses: list[PluginStatus]
    ) -> tuple[list[Plugin], list[PluginStatus]]:
        """Apply both the marketplace and status filters in a single pass.

        Returns a ``(plugins, statuses)`` pair restricted to rows that match
        both selections. ``"All"`` in either dropdown disables that filter.
        Used by the production refresh path; tests still exercise the
        marketplace-only legacy helpers below.
        """
        market_sel = self._filter_choice.GetStringSelection()
        status_sel = self._status_filter_choice.GetStringSelection()
        market_all = market_sel == "All"
        status_all = status_sel == _STATUS_FILTER_ALL
        if market_all and status_all:
            return list(plugins), list(statuses)

        out_p: list[Plugin] = []
        out_s: list[PluginStatus] = []
        for p, s in zip(plugins, statuses):
            if not market_all and p.marketplace != market_sel:
                continue
            if not status_all and s.value != status_sel:
                continue
            out_p.append(p)
            out_s.append(s)
        return out_p, out_s

    def _apply_filter(self, plugins: list[Plugin]) -> list[Plugin]:
        # Marketplace-only filter (legacy single-axis helper). Kept for
        # focused unit tests; production callers should use _filter_pair
        # so the status dropdown is honored too.
        sel = self._filter_choice.GetStringSelection()
        if sel == "All":
            return plugins
        return [p for p in plugins if p.marketplace == sel]

    def _apply_filter_statuses(
        self, plugins: list[Plugin], statuses: list[PluginStatus]
    ) -> list[PluginStatus]:
        sel = self._filter_choice.GetStringSelection()
        if sel == "All":
            return statuses
        return [s for p, s in zip(plugins, statuses) if p.marketplace == sel]

    # ------------------------------------------------------------- events

    def _bind_events(self) -> None:
        self.Bind(wx.EVT_MENU, self._on_reload, self._menu_reload)
        self.Bind(wx.EVT_MENU, self._on_add_plugin, self._menu_add_plugin)
        self.Bind(wx.EVT_MENU, self._on_marketplaces, self._menu_marketplaces)
        self.Bind(wx.EVT_MENU, self._on_update_all_plugins, self._menu_update_all_plugins)
        self.Bind(wx.EVT_MENU, self._on_uninstall_all_plugins, self._menu_uninstall_all_plugins)
        self.Bind(wx.EVT_MENU, self._on_update_all_marketplaces, self._menu_update_all_marketplaces)
        self.Bind(wx.EVT_MENU, self._on_remove_all_marketplaces, self._menu_remove_all_marketplaces)
        self.Bind(wx.EVT_MENU, self._on_reset_everything, self._menu_reset_everything)
        self.Bind(wx.EVT_MENU, self._on_exit, id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_about, id=wx.ID_ABOUT)

        self._filter_choice.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        self._status_filter_choice.Bind(wx.EVT_CHOICE, self._on_status_filter_changed)
        self._action_radio.Bind(wx.EVT_RADIOBOX, self._on_action_changed)

        self._plugin_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_selection_changed)
        self._plugin_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._on_selection_changed)

        self._btn_select.Bind(wx.EVT_BUTTON, self._on_select_all)
        self._btn_deselect.Bind(wx.EVT_BUTTON, self._on_deselect_all)
        self._btn_refresh.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._btn_execute.Bind(wx.EVT_BUTTON, self._on_execute)
        self._btn_cancel.Bind(wx.EVT_BUTTON, self._on_cancel)

        # Escape cancels a running job (no-op when idle).
        cancel_id = wx.NewIdRef()
        self.Bind(wx.EVT_MENU, self._on_cancel, id=cancel_id)
        accel = wx.AcceleratorTable(
            [
                (wx.ACCEL_NORMAL, wx.WXK_ESCAPE, cancel_id),
            ]
        )
        self.SetAcceleratorTable(accel)

        self.Bind(wx.EVT_CLOSE, self._on_close)

    # ----- First-show handler

    def _on_first_show(self, evt: wx.ShowEvent) -> None:
        # EVT_SHOW fires multiple times; only the first IsShown() call should
        # trigger the kickoff refresh. Unbind after first run.
        if evt.IsShown():
            self.Unbind(wx.EVT_SHOW)
            wx.CallAfter(self._refresh_from_cli)
            wx.CallAfter(self._verify_marketplaces_async)
        evt.Skip()

    # ----- Upstream-manifest verification (background, non-blocking)

    def _verify_marketplaces_async(self) -> None:
        """Fire-and-forget background check of declared marketplace names.

        Logs a warning to the log pane and announces via the live region if
        any declared marketplace's ``name`` doesn't match the upstream's
        ``marketplace.json:name``. Network failures are silent — the user
        just doesn't get warnings, which is the right behavior offline.
        """
        sources = [m for m in self._config.marketplaces if m.source]
        if not sources or self._closing:
            return

        def worker() -> None:
            from ..manifest_verifier import verify_marketplaces

            # Hard-guard the verifier so an unexpected exception in one source's
            # fetch path can't kill verification for the rest of the run, AND
            # can't leak out to the daemon thread (which would just log on
            # stderr and not surface in the UI). Audit I-7.
            try:
                results = verify_marketplaces(sources)
            except Exception:  # noqa: BLE001 — best-effort verification
                results = []
            wx.CallAfter(self._on_verifier_done, results)

        threading.Thread(target=worker, daemon=True).start()

    def _on_verifier_done(self, results: list) -> None:
        if self._closing:
            return
        from ..manifest_verifier import VerifierResult

        mismatches: list[VerifierResult] = [
            r
            for r in results
            if isinstance(r, VerifierResult) and r.canonical_name and not r.matches
        ]
        unverifiable = [
            r for r in results if isinstance(r, VerifierResult) and r.canonical_name is None
        ]

        if mismatches:
            for r in mismatches:
                self._log.append(
                    "WARN",
                    f"manifest mismatch: plugins.json declares {r.declared_name!r} "
                    f"but upstream {r.source} publishes name {r.canonical_name!r}",
                )
            # When there's exactly one mismatch, embed the detail directly so
            # screen-reader users get the actionable info without navigating
            # to the log. Otherwise point them at the log via Alt+G mnemonic
            # (a11y review I-5).
            if len(mismatches) == 1:
                r = mismatches[0]
                self._live.announce(
                    f"Marketplace name mismatch: plugins.json declares "
                    f"{r.declared_name!r} but upstream publishes {r.canonical_name!r}. "
                    "Press Alt+G for log."
                )
            else:
                self._live.announce(
                    f"{len(mismatches)} marketplace name mismatches. Press Alt+G for log."
                )
        if unverifiable:
            # Quiet info: not every source can be auto-checked (local paths,
            # non-GitHub URLs, offline). Log once with a count.
            self._log.append(
                "INFO",
                f"upstream manifest auto-verify skipped for {len(unverifiable)} "
                f"marketplace{'s' if len(unverifiable) != 1 else ''} "
                f"(non-GitHub source or network unavailable)",
            )

    # ----- Menu handlers

    def _on_exit(self, _evt: wx.CommandEvent) -> None:
        self.Close()

    def _on_about(self, _evt: wx.CommandEvent) -> None:
        wx.MessageBox(
            "Claude Code Plugin Manager\n\nAccessible wxPython front end for the Claude CLI.",
            "About",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def _on_add_plugin(self, _evt: wx.CommandEvent) -> None:
        # Block during runs so the file isn't being written while the worker
        # could be reading it (the worker doesn't reload mid-run today, but
        # this is defensive).
        if self._worker is not None and self._worker.is_alive():
            wx.MessageBox(
                "Wait for the current run to finish before adding plugins.",
                "Run in progress",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return

        from ..__main__ import _resolve_config_path
        from ..data import write_config
        from .add_plugin_dialog import AddPluginDialog

        existing_ids = {p.qualified_id for p in self._config.plugins}
        with AddPluginDialog(
            self,
            declared_marketplaces=list(self._config.marketplaces),
            existing_plugin_ids=existing_ids,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK or dlg.picked is None:
                return
            new_plugin = dlg.picked

        path = _resolve_config_path()
        # Build a new Config with the plugin appended; let the writer
        # alphabetize and the loader re-validate (catches undeclared
        # marketplace refs at the same place every other write would).
        new_config = type(self._config)(
            marketplaces=list(self._config.marketplaces),
            plugins=list(self._config.plugins) + [new_plugin],
        )
        try:
            write_config(path, new_config)
        except OSError as exc:
            wx.MessageBox(
                f"Could not write {path}:\n\n{exc}",
                "Save failed",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        self._log.append("INFO", f"added {new_plugin.qualified_id} to {path}")
        self._live.announce(f"Added {new_plugin.qualified_id}")
        # Reload through the standard path so validators run and the UI
        # refreshes consistently.
        self._reload_config_and_view(path)

    def _reload_config_and_view(self, path: str) -> None:
        try:
            self._config = load_config(path)
        except (ConfigError, OSError) as exc:
            wx.MessageBox(
                f"Reload after edit failed:\n{exc}",
                "Reload error",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        # Rebuild marketplace filter choices, preserving the selection if
        # still valid.
        sel = self._filter_choice.GetStringSelection()
        new_choices = self._filter_choices()
        self._filter_choice.SetItems(new_choices)
        if sel in new_choices:
            self._filter_choice.SetStringSelection(sel)
        else:
            self._filter_choice.SetSelection(0)
        # Preserve selection across reload — common workflow is "add a plugin,
        # then immediately Execute on the previously-selected set" (audit M-10).
        self._populate_plugins_initial(preserve_selection=True)
        self._refresh_from_cli()

    # ----- Advanced menu (destructive bulk operations)

    def _confirm_destructive(self, title: str, message: str) -> bool:
        """Yes/No confirmation with WARNING icon. Default: No.

        Returns True only if the user explicitly confirmed.
        """
        dlg = wx.MessageDialog(
            self,
            message,
            title,
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
        )
        try:
            return dlg.ShowModal() == wx.ID_YES
        finally:
            dlg.Destroy()

    def _bulk_run_guarded(
        self, ops: list[Operation], started_label: str, *, announce_label: str = ""
    ) -> None:
        """Common entry for Advanced-menu actions: refuse if a run is in flight,
        no-op (with a friendly dialog) when the queue is empty, otherwise hand
        off to ``_start_run`` so progress / cancel / log all work uniformly.

        ``started_label`` is the log-line prefix (machine-readable). ``announce_label``
        is what the live region speaks at run start; pass a user-facing variant
        like "Uninstalling all 30 plugins" so screen-reader users know which
        run kicked off.
        """
        if self._worker is not None and self._worker.is_alive():
            wx.MessageBox(
                "Wait for the current run to finish.",
                "Run in progress",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        if not ops:
            wx.MessageBox(
                "Nothing to do.",
                "No operations",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        self._log.append("INFO", started_label)
        self._start_run(ops, label=announce_label)

    def _installed_as_plugins(self, scope_filter: str) -> list:
        """Convert the latest CLI snapshot into ``(Plugin, status)`` tuples.

        ``scope_filter`` keeps only entries that match the user's chosen scope
        on the main UI (so an Advanced "uninstall all" at scope=user doesn't
        try to operate on project-scoped installs). Rows where the CLI didn't
        report a scope are SKIPPED with a one-line INFO log — silently
        including them would let "Uninstall all at scope=user" target a
        project-scoped install (audit I-2).
        """
        if not self._installed:
            return []
        plugins: list = []
        skipped_no_scope = 0
        for ip in self._installed:
            if ip.scope is None:
                skipped_no_scope += 1
                continue
            if ip.scope != scope_filter:
                continue
            plugins.append(
                (
                    Plugin(name=ip.name, marketplace=ip.marketplace),
                    PluginStatus.INSTALLED,
                )
            )
        if skipped_no_scope:
            self._log.append(
                "INFO",
                f"{skipped_no_scope} installed plugin{'s' if skipped_no_scope != 1 else ''} "
                "had no scope reported by the CLI and were skipped from the bulk operation",
            )
        return plugins

    def _on_update_all_plugins(self, _evt: wx.CommandEvent) -> None:
        scope = self._scope_choice.GetStringSelection() or "user"
        targets = self._installed_as_plugins(scope_filter=scope)
        if not targets:
            wx.MessageBox(
                f"No installed plugins found in scope {scope!r}. "
                "Refresh first or change scope, then retry.",
                "Nothing to update",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        if not self._confirm_destructive(
            "Update all plugins",
            f"Update all {len(targets)} installed plugins in scope {scope!r}?\n\n"
            "Each plugin is updated sequentially. Press Escape during the run to cancel.",
        ):
            return
        ops = build_operations(
            action=ActionKind.UPDATE,
            scope=scope,
            selected=targets,
            config=self._config,
            present_markets=self._present_markets,
        )
        self._bulk_run_guarded(
            ops,
            f"--- update all plugins ({len(targets)}) ---",
            announce_label=f"Updating all {len(targets)} plugins in scope {scope}",
        )

    def _on_uninstall_all_plugins(self, _evt: wx.CommandEvent) -> None:
        scope = self._scope_choice.GetStringSelection() or "user"
        # Uninstall doesn't allow the "managed" scope.
        if scope not in ("user", "project", "local"):
            scope = "user"
        targets = self._installed_as_plugins(scope_filter=scope)
        if not targets:
            wx.MessageBox(
                f"No installed plugins found in scope {scope!r}.",
                "Nothing to uninstall",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        if not self._confirm_destructive(
            "Uninstall all plugins",
            f"Uninstall all {len(targets)} installed plugins from scope {scope!r}?\n\n"
            "This cannot be undone. Press Escape during the run to cancel.",
        ):
            return
        ops = build_operations(
            action=ActionKind.UNINSTALL,
            scope=scope,
            selected=targets,
            config=self._config,
            present_markets=self._present_markets,
        )
        self._bulk_run_guarded(
            ops,
            f"--- uninstall all plugins ({len(targets)}) ---",
            announce_label=f"Uninstalling all {len(targets)} plugins from scope {scope}",
        )

    def _on_update_all_marketplaces(self, _evt: wx.CommandEvent) -> None:
        # Single CLI invocation (`marketplace update` with no name) handles
        # all of them. We still go through the worker so progress / cancel /
        # log work uniformly.
        ops: list[Operation] = [MarketplaceUpdateOp(name=None)]
        if not self._confirm_destructive(
            "Update all marketplaces",
            "Refresh every registered marketplace from its source?\n\n"
            "This is generally safe (no plugins are touched).",
        ):
            return
        self._bulk_run_guarded(
            ops,
            "--- update all marketplaces ---",
            announce_label="Updating all marketplaces",
        )

    def _on_remove_all_marketplaces(self, _evt: wx.CommandEvent) -> None:
        registered = sorted(self._present_markets)
        if not registered:
            wx.MessageBox(
                "No registered marketplaces to remove.",
                "Nothing to remove",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        if not self._confirm_destructive(
            "Remove all marketplaces",
            f"Remove all {len(registered)} registered marketplaces from the Claude CLI?\n\n"
            "This does not delete plugins.json. Plugins from removed marketplaces will\n"
            "show 'marketplace missing' until you re-add the marketplace or the worker\n"
            "auto-adds it on the next install.",
        ):
            return
        ops: list[Operation] = [MarketplaceRemoveOp(name=name) for name in registered]
        self._bulk_run_guarded(
            ops,
            f"--- remove all marketplaces ({len(registered)}) ---",
            announce_label=f"Removing all {len(registered)} marketplaces",
        )

    def _on_reset_everything(self, _evt: wx.CommandEvent) -> None:
        scope = self._scope_choice.GetStringSelection() or "user"
        if scope not in ("user", "project", "local"):
            scope = "user"
        plugin_targets = self._installed_as_plugins(scope_filter=scope)
        marketplaces = sorted(self._present_markets)
        if not plugin_targets and not marketplaces:
            wx.MessageBox(
                "Nothing to reset — no installed plugins or registered marketplaces.",
                "Already empty",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        # Sentence-per-line phrasing reads naturally with NVDA's line-by-line
        # navigation (a11y review I-2). Title includes the scope so the user
        # is never surprised that a project-scope install survived (a11y M-1).
        # Marketplace removal is intentionally NOT scope-filtered — the
        # message spells that out so "scope=user" doesn't mislead users into
        # thinking project-scoped installs and their marketplaces are
        # untouched (audit I-3).
        if not self._confirm_destructive(
            f"Reset everything in scope {scope!r}",
            f"This is a destructive operation. It will:\n"
            f"Uninstall the {len(plugin_targets)} installed plugins in scope {scope!r}.\n"
            f"Remove all {len(marketplaces)} registered marketplaces (regardless of scope).\n"
            f"Plugins installed in other scopes will not be uninstalled, but their\n"
            "marketplaces will still be removed and they may show 'marketplace missing'.\n"
            "plugins.json is not modified. This cannot be undone. Continue?",
        ):
            return
        plugin_ops = build_operations(
            action=ActionKind.UNINSTALL,
            scope=scope,
            selected=plugin_targets,
            config=self._config,
            present_markets=self._present_markets,
        )
        marketplace_ops: list[Operation] = [MarketplaceRemoveOp(name=name) for name in marketplaces]
        # Uninstall plugins FIRST, then drop the marketplaces — uninstalling
        # after the marketplace is gone fails on some CLI versions.
        ops: list[Operation] = list(plugin_ops) + marketplace_ops
        self._bulk_run_guarded(
            ops,
            f"--- reset: {len(plugin_targets)} plugins + {len(marketplaces)} marketplaces ---",
            announce_label=(
                f"Resetting: uninstalling {len(plugin_targets)} plugins from scope {scope}, "
                f"then removing {len(marketplaces)} marketplaces"
            ),
        )

    # ----- Marketplaces dialog

    def _on_marketplaces(self, _evt: wx.CommandEvent) -> None:
        # Don't open the dialog mid-run — it would race with the main worker
        # over the same CLI executable.
        if self._worker is not None and self._worker.is_alive():
            wx.MessageBox(
                "Wait for the current run to finish before managing marketplaces.",
                "Run in progress",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return

        # Lazy import — keeps the dialog out of the cold-import path.
        from .marketplace_dialog import MarketplaceDialog

        with MarketplaceDialog(
            self,
            cli=self._cli,
            declared=list(self._config.marketplaces),
            registered=self._present_markets,
        ) as dlg:
            dlg.ShowModal()
            changed = dlg.changed()

        # If the dialog mutated the CLI registry, refresh main view so plugin
        # status (especially MARKETPLACE_MISSING) reflects reality.
        if changed:
            self._log.append("INFO", "marketplaces changed; refreshing")
            self._refresh_from_cli()

    def _on_reload(self, _evt: wx.CommandEvent) -> None:
        # Use the same resolution as startup (env > CWD > bundled fallback)
        # so reload behaves identically to the initial load.
        from ..__main__ import _resolve_config_path

        path = _resolve_config_path()
        try:
            new_config = load_config(path)
        except (ConfigError, OSError) as exc:
            wx.MessageBox(
                f"Failed to reload {path}:\n{exc}",
                "Reload error",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            self._log.append("ERR", f"reload failed: {exc}")
            return

        self._config = new_config
        # Rebuild filter choices.
        sel = self._filter_choice.GetStringSelection()
        new_choices = self._filter_choices()
        self._filter_choice.SetItems(new_choices)
        if sel in new_choices:
            self._filter_choice.SetStringSelection(sel)
        else:
            self._filter_choice.SetSelection(0)
        # Preserve selection across manual File → Reload (audit M-10).
        self._populate_plugins_initial(preserve_selection=True)
        self._log.append("INFO", f"reloaded {path}")
        self._live.announce("Configuration reloaded")
        self._refresh_from_cli()

    # ----- Filter & action handlers

    def _on_filter_changed(self, _evt: wx.CommandEvent) -> None:
        self._apply_refresh()
        sel = self._filter_choice.GetStringSelection()
        count = self._plugin_list.GetItemCount()
        self._live.announce(f"Marketplace {sel}: {count} plugins")

    def _on_status_filter_changed(self, _evt: wx.CommandEvent) -> None:
        self._apply_refresh()
        sel = self._status_filter_choice.GetStringSelection()
        count = self._plugin_list.GetItemCount()
        self._live.announce(f"Status {sel}: {count} plugins")

    def _on_action_changed(self, _evt: wx.CommandEvent) -> None:
        action = list(ActionKind)[self._action_radio.GetSelection()]
        scopes = SCOPES_BY_ACTION[action]
        current = self._scope_choice.GetStringSelection()
        self._scope_choice.SetItems(list(scopes))
        if current in scopes:
            self._scope_choice.SetStringSelection(current)
        else:
            self._scope_choice.SetSelection(0)
        self._live.announce(f"Action {action.value}")

    # ----- Selection handlers

    def _on_select_all(self, _evt: wx.CommandEvent) -> None:
        self._plugin_list.set_all_checked(True)
        count = self._plugin_list.GetItemCount()
        self._update_selection_status()
        self._live.announce(f"Selected {count} plugins")

    def _on_deselect_all(self, _evt: wx.CommandEvent) -> None:
        self._plugin_list.set_all_checked(False)
        self._update_selection_status()
        self._live.announce("Deselected all plugins")

    def _on_selection_changed(self, evt: wx.ListEvent) -> None:
        self._update_selection_status()
        evt.Skip()

    def _update_selection_status(self) -> None:
        n = len(self._plugin_list.checked_plugins())
        self.SetStatusText(f"{n} selected", 1)

    def _on_refresh(self, _evt: wx.CommandEvent) -> None:
        self._refresh_from_cli()

    # ----- Close handler

    def _on_close(self, evt: wx.CloseEvent) -> None:
        # IMPORTANT: do NOT set ``self._closing = True`` before the user
        # confirms — while the modal confirmation dialog is up, worker
        # events would be silently dropped, corrupting tallies and gauge
        # state when the user picks "No" (audit C-2).
        if self._worker is not None and self._worker.is_alive():
            if evt.CanVeto():
                resp = wx.MessageBox(
                    "An action is in progress. Cancel and exit?",
                    "Confirm exit",
                    wx.YES_NO | wx.ICON_QUESTION,
                    self,
                )
                if resp != wx.YES:
                    evt.Veto()
                    return
            self._closing = True
            self._worker.cancel()
            # Cooperative cancellation completes the current op then breaks.
            # Briefly join so the worker's tail events drain before Destroy.
            self._worker.join(timeout=_CLOSE_WAIT_SECS)
        else:
            self._closing = True
        self.Destroy()

    # --------------------------------------------------------- CLI refresh

    def _refresh_from_cli(self) -> None:
        if self._closing:
            return
        self._refresh_gen += 1
        gen = self._refresh_gen
        if not self._refresh_in_flight:
            self._live.announce("Refreshing status from Claude CLI")
            self._log.append("INFO", "querying claude plugin list / marketplace list")
        self._refresh_in_flight = True
        self._btn_refresh.Disable()

        def worker() -> None:
            try:
                installed = self._cli.list_plugins()
                markets = self._cli.list_marketplaces()
            except Exception as exc:  # noqa: BLE001 — surface in UI
                wx.CallAfter(self._on_refresh_error, gen, str(exc))
                return
            wx.CallAfter(self._on_refresh_ok, gen, installed, markets)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_is_stale(self, gen: int) -> bool:
        return self._closing or gen != self._refresh_gen

    def _refresh_finished(self) -> None:
        self._refresh_in_flight = False
        # Don't re-enable Refresh if a worker run started while we were
        # in flight — _set_running_state(True) deliberately disabled it,
        # and re-enabling here would let the user spawn a concurrent
        # `claude` subprocess that races the worker (audit I-1).
        if self._closing:
            return
        if self._worker is not None and self._worker.is_alive():
            return
        self._btn_refresh.Enable()

    def _on_refresh_error(self, gen: int, message: str) -> None:
        if self._refresh_is_stale(gen):
            return
        self._log.append("ERR", f"refresh failed: {message}")
        self._live.announce("Refresh failed")
        self.SetStatusText("Refresh failed")
        self._refresh_finished()

    def _on_refresh_ok(
        self,
        gen: int,
        installed: Optional[list[InstalledPlugin]],
        markets: Optional[set[str]],
    ) -> None:
        if self._refresh_is_stale(gen):
            return
        if installed is None or markets is None:
            self._installed = None
            self._present_markets = set()
            self._apply_refresh()
            self.SetStatusText("Refresh failed")
            self._live.announce("Refresh failed")
            self._log.append("ERR", "refresh failed: CLI did not return data")
            self._refresh_finished()
            return

        self._installed = list(installed)
        self._present_markets = set(markets)
        self._apply_refresh()
        self.SetStatusText(
            f"{len(self._installed)} installed / {len(self._present_markets)} marketplaces"
        )
        self._live.announce("Status refreshed")
        self._log.append(
            "INFO",
            f"refresh OK ({len(self._installed)} plugins, {len(self._present_markets)} marketplaces)",
        )
        self._refresh_finished()

    def _apply_refresh(self) -> None:
        plugins = list(self._config.plugins)
        statuses = [
            derive_status(p, self._config, self._installed, self._present_markets) for p in plugins
        ]
        filtered_plugins, filtered_statuses = self._filter_pair(plugins, statuses)
        self._plugin_list.set_rows(filtered_plugins, filtered_statuses)
        self._update_selection_status()

    # ------------------------------------------------------------ run flow

    def _on_execute(self, _evt: wx.CommandEvent) -> None:
        if self._worker is not None and self._worker.is_alive():
            return

        action = list(ActionKind)[self._action_radio.GetSelection()]
        scope = self._scope_choice.GetStringSelection() or "user"

        checked = self._plugin_list.checked_plugins()
        if not checked:
            wx.MessageBox(
                "Select one or more plugins first.",
                "Nothing selected",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return

        # checked_plugins() already returns list[tuple[Plugin, PluginStatus]].
        ops = build_operations(
            action=action,
            scope=scope,
            selected=checked,
            config=self._config,
            present_markets=self._present_markets,
        )
        if not ops:
            wx.MessageBox("Nothing to do.", "No operations", wx.OK | wx.ICON_INFORMATION, self)
            return

        self._start_run(ops)

    def _start_run(self, ops: list[object], *, label: str = "") -> None:
        self._gauge.SetRange(len(ops))
        self._gauge.SetValue(0)
        # Reset per-kind tallies for the upcoming run.
        self._run_label = label
        self._run_plugin_ok = 0
        self._run_plugin_fail = 0
        self._run_plugin_skip = 0
        self._run_marketplace_ok = 0
        self._run_marketplace_fail = 0
        self._last_announce_monotonic = time.monotonic()

        self._set_running_state(True)
        self._log.append(
            "INFO", f"--- run started at {datetime.now().isoformat(timespec='seconds')} ---"
        )
        # Tailor the start announcement so screen-reader users immediately
        # hear what kind of run was kicked off (a11y review M-2).
        if label:
            self._live.announce(f"{label}. Press Escape to cancel.")
        else:
            self._live.announce(f"Running {len(ops)} operations. Press Escape to cancel.")

        def post(evt: object) -> None:
            wx.CallAfter(self._on_worker_event, evt)

        self._worker = ExecutionWorker(cli=self._cli, ops=ops, post_event=post)
        self._worker.start()

    def _on_cancel(self, _evt: wx.CommandEvent) -> None:
        if self._worker is not None and self._worker.is_alive():
            self._worker.cancel()
            self._log.append("INFO", "cancel requested")
            self._live.announce("Cancellation requested")

    def _set_running_state(self, running: bool) -> None:
        if running:
            # Park focus on Cancel before disabling other controls; otherwise
            # if focus was on a control we're about to disable (e.g. Execute),
            # wx will hop to the next focusable sibling, which may also be
            # disabled, leaving focus orphaned at the frame root.
            self._btn_cancel.Enable(True)
            self._btn_cancel.SetFocus()

        self._btn_execute.Enable(not running)
        self._btn_cancel.Enable(running)
        self._btn_refresh.Enable(not running)
        self._plugin_list.Enable(not running)
        self._btn_select.Enable(not running)
        self._btn_deselect.Enable(not running)
        self._action_radio.Enable(not running)
        self._scope_choice.Enable(not running)
        self._filter_choice.Enable(not running)
        self._status_filter_choice.Enable(not running)
        self._menu_reload.Enable(not running)
        self._menu_add_plugin.Enable(not running)
        self._menu_marketplaces.Enable(not running)
        for item in (
            self._menu_update_all_plugins,
            self._menu_uninstall_all_plugins,
            self._menu_update_all_marketplaces,
            self._menu_remove_all_marketplaces,
            self._menu_reset_everything,
        ):
            item.Enable(not running)

    def _on_worker_event(self, evt: object) -> None:
        if self._closing:
            return
        if isinstance(evt, ProgressEvent):
            self._on_progress_event(evt)
            return
        if isinstance(evt, OpResultEvent):
            self._log_op_result(evt)
            return
        if isinstance(evt, RunCompleteEvent):
            self._finish_run(evt)
            return

    def _on_progress_event(self, evt: ProgressEvent) -> None:
        self._gauge.SetValue(evt.index)
        if not isinstance(evt.op, SkipOp):
            cmd = cmd_for(evt.op, self._cli.executable)
            self._log.append("RUN", " ".join(cmd))
        # Milestone-only announcements with a time-based safety net so a long
        # destructive run never goes silent (a11y review C-2).
        now = time.monotonic()
        elapsed_since_announce = now - self._last_announce_monotonic
        should_announce = (
            evt.index == 1
            or evt.index == evt.total
            or evt.index % _PROGRESS_STRIDE == 0
            or isinstance(evt.op, SkipOp)
            or elapsed_since_announce >= _ANNOUNCE_FALLBACK_SECS
        )
        if should_announce:
            self._live.announce(f"{evt.op.label} ({evt.index} of {evt.total})")
            self._last_announce_monotonic = now

    def _log_op_result(self, evt: OpResultEvent) -> None:
        label = _result_label(evt.status)
        op = evt.op
        if isinstance(op, MarketplaceAddOp):
            target = f"marketplace add {op.name}"
        elif isinstance(op, MarketplaceRemoveOp):
            target = f"marketplace remove {op.name}"
        elif isinstance(op, MarketplaceUpdateOp):
            target = "marketplace update " + ("(all)" if op.name is None else op.name)
        elif isinstance(op, PluginOp):
            target = f"{op.action.value} {op.plugin.qualified_id}"
        elif isinstance(op, SkipOp):
            target = f"skip {op.plugin.qualified_id}"
        else:
            target = op.label

        duration = f" ({evt.duration:.2f}s)" if evt.duration else ""
        self._log.append(label, f"{target}{duration}")

        if evt.status == OpStatus.SKIP and evt.stderr:
            self._log.append("INFO", f"  reason: {evt.stderr}")
        else:
            for line in (evt.stdout or "").splitlines():
                if line.strip():
                    self._log.append_continuation("|", line)
            for line in (evt.stderr or "").splitlines():
                if line.strip():
                    self._log.append_continuation("!", line)

        # Per-kind tally so _finish_run can compose a summary that
        # distinguishes plugin outcomes from marketplace outcomes for
        # mixed-kind runs like Reset Everything (a11y review C-1).
        is_marketplace_op = isinstance(
            op, (MarketplaceAddOp, MarketplaceRemoveOp, MarketplaceUpdateOp)
        )
        if evt.status == OpStatus.OK:
            if is_marketplace_op:
                self._run_marketplace_ok += 1
            else:
                self._run_plugin_ok += 1
        elif evt.status in (OpStatus.FAIL, OpStatus.TIMEOUT):
            if is_marketplace_op:
                self._run_marketplace_fail += 1
            else:
                self._run_plugin_fail += 1
        elif evt.status == OpStatus.SKIP:
            # Only PluginOp routes through SkipOp today, but be defensive.
            self._run_plugin_skip += 1

        # Announce only failures (and timeouts). Successful per-op results
        # would flood the speech queue; the summary at run end covers totals.
        if evt.status in (OpStatus.FAIL, OpStatus.TIMEOUT):
            self._live.announce(f"{label}: {op.label}")
            self._last_announce_monotonic = time.monotonic()

    def _finish_run(self, evt: RunCompleteEvent) -> None:
        self._set_running_state(False)
        self._worker = None

        if evt.error:
            self._log.append("ERR", f"worker error: {evt.error}")

        # Build a status-bar summary (single line) and a dialog body
        # (potentially multi-line) that distinguishes plugin outcomes from
        # marketplace outcomes when the run touched both (a11y review C-1).
        summary, body_lines = self._compose_summary(evt)
        self._log.append("INFO", summary)
        self.SetStatusText(summary)
        self._live.announce(summary)

        if evt.error:
            icon = wx.ICON_ERROR
            title = "Run failed"
            body = "\n".join(body_lines + ["", f"Error: {evt.error}"])
        elif evt.failed:
            icon = wx.ICON_WARNING
            title = "Run completed with failures"
            body = "\n".join(body_lines + ["", "Check the Log pane for details."])
        elif evt.cancelled:
            icon = wx.ICON_WARNING
            title = "Run cancelled"
            body = "\n".join(body_lines)
        else:
            icon = wx.ICON_INFORMATION
            title = "Run complete"
            body = "\n".join(body_lines)

        # Show modal first; on dismissal, set focus on Execute, then schedule
        # the post-run refresh. This prevents three overlapping announcements
        # (dialog close + focus restore + refresh start) and guarantees
        # focus lands somewhere predictable.
        wx.MessageBox(body, title, wx.OK | icon, self)
        self._btn_execute.SetFocus()
        wx.CallAfter(self._refresh_from_cli)

    def _compose_summary(self, evt: RunCompleteEvent) -> tuple[str, list[str]]:
        """Return ``(status_bar_text, dialog_body_lines)`` for the run.

        Plugin and marketplace outcomes are reported separately in the dialog
        body when the run touched both kinds (Reset Everything in particular).
        The single-line status string always uses the worker's totals
        (``evt.succeeded`` etc.) — they're authoritative even if the per-kind
        UI tallies got out of sync (audit M-1). The phrasing matches the
        legacy format users may have log-scraped.
        """
        cancelled_suffix = " (cancelled)" if evt.cancelled else ""
        plugin_total = self._run_plugin_ok + self._run_plugin_fail + self._run_plugin_skip
        marketplace_total = self._run_marketplace_ok + self._run_marketplace_fail

        # Authoritative single-line summary — worker totals, legacy phrasing.
        status = (
            f"Done. {evt.succeeded} ok, {evt.failed} failed, "
            f"{evt.skipped} skipped{cancelled_suffix}"
        )

        # Multi-line dialog body: split into "Plugins:" / "Marketplaces:" rows
        # when both kinds were touched. Otherwise reuse the status line.
        body_lines: list[str]
        if plugin_total and marketplace_total:
            body_lines = [
                f"Plugins: {self._run_plugin_ok} ok, "
                f"{self._run_plugin_fail} failed, {self._run_plugin_skip} skipped.",
                f"Marketplaces: {self._run_marketplace_ok} ok, "
                f"{self._run_marketplace_fail} failed.",
            ]
            if evt.cancelled:
                body_lines.append("Run was cancelled.")
        elif marketplace_total and not plugin_total:
            body_lines = [
                f"Marketplaces: {self._run_marketplace_ok} ok, "
                f"{self._run_marketplace_fail} failed."
                + (" Run was cancelled." if evt.cancelled else "")
            ]
        else:
            body_lines = [status]
        return status, body_lines
