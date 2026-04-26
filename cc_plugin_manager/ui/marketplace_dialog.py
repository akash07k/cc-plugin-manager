"""Accessible marketplace management dialog.

Opened via File → Marketplaces… (Ctrl+M). Shows the union of:

- Marketplaces declared in ``plugins.json`` (the curated set the app knows
  how to auto-add).
- Marketplaces the ``claude`` CLI has registered (the runtime set).

Each row's Status column reflects which of those two sets it belongs to,
making drift between the two visible. Add / Remove / Update / Update All /
Refresh operate on the **CLI registry**; they do not edit ``plugins.json``.
A note in the dialog footer makes that explicit.

Threading: every CLI call runs on a short-lived daemon thread, with results
posted back to the dialog via ``wx.CallAfter``. The dialog disables its
action buttons while a CLI call is in flight, parking focus on Close so
keyboard users never land on a disabled button. A live region announces
operation start and result (success / failure).

Accessibility patterns mirror the main frame:

- Native ``wx.ListCtrl`` in ``LC_REPORT`` mode (UIA-friendly selection).
- Mnemonics on every button: &Add..., &Remove, &Update, Update &All,
  Re&fresh, &Close. None collide.
- Focus on the list when the dialog opens.
- Esc dismisses (built-in wxDialog behavior).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

import wx

from ..cli import ClaudeCli, CliResult
from ..data import Marketplace
from .live_region import LiveRegion


_SCOPES = ("user", "project", "local")


@dataclass(frozen=True)
class _Row:
    name: str
    source: str  # "(built-in)" if no source declared
    declared: bool  # in plugins.json
    registered: bool  # in `claude plugin marketplace list`

    @property
    def status(self) -> str:
        # Plain-language for screen-reader scanability (a11y review I-4):
        # NVDA reads each row left-to-right; "declared, registered" forces
        # users to remember what each word means in this app's vocabulary.
        if self.declared and self.registered:
            return "both"
        if self.declared:
            return "plugins.json only"
        if self.registered:
            return "CLI only"
        return "unknown"


class MarketplaceDialog(wx.Dialog):
    COL_NAME = 0
    COL_SOURCE = 1
    COL_STATUS = 2

    def __init__(
        self,
        parent: wx.Window,
        *,
        cli: ClaudeCli,
        declared: list[Marketplace],
        registered: Optional[set[str]],
    ) -> None:
        super().__init__(
            parent,
            title="Marketplaces",
            size=(720, 480),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetMinSize((620, 360))
        self._cli = cli
        self._declared = list(declared)
        self._registered: set[str] = set(registered) if registered else set()
        self._busy = False
        self._closing = False

        self._build_widgets()
        self._build_layout()
        self._bind_events()
        self._populate_rows()

        # Land focus on the list so screen-reader users hear "list, marketplace
        # foo, registered" right away rather than the first button.
        self._list.SetFocus()

    # --------------------------------------------------------- widgets

    def _build_widgets(self) -> None:
        panel = wx.Panel(self)
        self._panel = panel

        self._list_label = wx.StaticText(panel, label="&Marketplaces")
        self._list = wx.ListCtrl(
            panel,
            style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES | wx.LC_SINGLE_SEL,
        )
        self._list.SetName("Marketplaces")
        self._list.InsertColumn(self.COL_NAME, "Name", width=self.FromDIP(220))
        self._list.InsertColumn(self.COL_SOURCE, "Source", width=self.FromDIP(280))
        self._list.InsertColumn(self.COL_STATUS, "Status", width=self.FromDIP(180))

        # Mnemonics within this dialog: A (Add), R (Remove), U (Update),
        # L (Update Al&l — avoids A collision with Add), F (Refresh), C (Close).
        self._btn_add = wx.Button(panel, label="&Add...")
        self._btn_remove = wx.Button(panel, label="&Remove")
        self._btn_update = wx.Button(panel, label="&Update")
        self._btn_update_all = wx.Button(panel, label="Update Al&l")
        self._btn_refresh = wx.Button(panel, label="Re&fresh")
        self._btn_close = wx.Button(panel, id=wx.ID_CLOSE, label="&Close")

        self._note = wx.StaticText(
            panel,
            label=(
                "Add / Remove / Update operate on the Claude CLI registry. "
                "To persist changes across reloads, edit plugins.json."
            ),
        )
        self._live = LiveRegion(panel, label="Ready")

        # Per-selection enabled-ness: Remove and Update need a selection.
        self._update_button_state()

    def _build_layout(self) -> None:
        panel = self._panel

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(self._list_label, 0, wx.LEFT | wx.TOP, 6)
        outer.Add(self._list, 1, wx.EXPAND | wx.ALL, 6)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.Add(self._btn_add, 0, wx.RIGHT, 6)
        btn_row.Add(self._btn_remove, 0, wx.RIGHT, 6)
        btn_row.Add(self._btn_update, 0, wx.RIGHT, 6)
        btn_row.Add(self._btn_update_all, 0, wx.RIGHT, 6)
        btn_row.Add(self._btn_refresh, 0, wx.RIGHT, 6)
        btn_row.AddStretchSpacer(1)
        btn_row.Add(self._btn_close, 0)
        outer.Add(btn_row, 0, wx.EXPAND | wx.ALL, 6)

        outer.Add(self._note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        outer.Add(self._live, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        panel.SetSizer(outer)

        # Make Close the dialog's affirmative-default so Esc/Enter do the
        # right thing for keyboard users.
        self.SetAffirmativeId(wx.ID_CLOSE)
        self.SetEscapeId(wx.ID_CLOSE)

    def _bind_events(self) -> None:
        self._btn_add.Bind(wx.EVT_BUTTON, self._on_add)
        self._btn_remove.Bind(wx.EVT_BUTTON, self._on_remove)
        self._btn_update.Bind(wx.EVT_BUTTON, self._on_update)
        self._btn_update_all.Bind(wx.EVT_BUTTON, self._on_update_all)
        self._btn_refresh.Bind(wx.EVT_BUTTON, self._on_refresh)
        self._btn_close.Bind(wx.EVT_BUTTON, self._on_close_clicked)

        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_selection_changed)
        self._list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._on_selection_changed)

        self.Bind(wx.EVT_CLOSE, self._on_dialog_close)

    def _on_close_clicked(self, _evt: wx.CommandEvent) -> None:
        # EndModal does NOT fire EVT_CLOSE, so we must set _closing here too.
        # Otherwise an in-flight CallAfter (from _refresh_registered, _on_add,
        # etc.) would target the soon-to-be-destroyed dialog (audit C-1).
        self._closing = True
        self.EndModal(wx.ID_CLOSE)

    # --------------------------------------------------------- public-ish

    def changed(self) -> bool:
        """Whether anything was modified (caller refreshes the main view)."""
        return self._dirty

    # --------------------------------------------------------- helpers

    def _on_dialog_close(self, evt: wx.CloseEvent) -> None:
        self._closing = True
        evt.Skip()

    def _populate_rows(self) -> None:
        rows = self._compute_rows()
        self._rows: list[_Row] = rows
        self._list.DeleteAllItems()
        for i, r in enumerate(rows):
            self._list.InsertItem(i, r.name)
            self._list.SetItem(i, self.COL_SOURCE, r.source)
            self._list.SetItem(i, self.COL_STATUS, r.status)
        if rows:
            self._list.SetItemState(0, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
        self._update_button_state()

    def _compute_rows(self) -> list[_Row]:
        declared_by_name = {m.name: m for m in self._declared}
        names = sorted(set(declared_by_name) | self._registered)
        out: list[_Row] = []
        for name in names:
            m = declared_by_name.get(name)
            source = m.source if (m and m.source) else ("(built-in)" if m else "")
            out.append(
                _Row(
                    name=name,
                    source=source,
                    declared=name in declared_by_name,
                    registered=name in self._registered,
                )
            )
        return out

    def _selected_row(self) -> Optional[_Row]:
        idx = self._list.GetFirstSelected()
        if idx < 0 or idx >= len(self._rows):
            return None
        return self._rows[idx]

    def _on_selection_changed(self, evt: wx.ListEvent) -> None:
        self._update_button_state()
        evt.Skip()

    def _update_button_state(self) -> None:
        has_sel = self._list.GetFirstSelected() >= 0
        self._btn_remove.Enable(has_sel and not self._busy)
        self._btn_update.Enable(has_sel and not self._busy)
        self._btn_add.Enable(not self._busy)
        self._btn_update_all.Enable(not self._busy)
        self._btn_refresh.Enable(not self._busy)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            # Park focus on Close before disabling the action buttons,
            # mirroring the main-frame discipline so keyboard users never
            # end up on a disabled control.
            if (
                self._btn_remove.HasFocus()
                or self._btn_update.HasFocus()
                or self._btn_add.HasFocus()
                or self._btn_update_all.HasFocus()
                or self._btn_refresh.HasFocus()
            ):
                self._btn_close.SetFocus()
        self._update_button_state()

    @property
    def _dirty(self) -> bool:
        return getattr(self, "_dirty_flag", False)

    def _mark_dirty(self) -> None:
        self._dirty_flag = True

    # --------------------------------------------------------- async runner

    def _run_async(
        self,
        action_label: str,
        op: Callable[[], CliResult],
        on_done: Callable[[CliResult], None],
    ) -> None:
        """Run a CLI op on a background thread; deliver result on main thread."""
        self._set_busy(True)
        self._live.announce(f"{action_label}…")

        def worker() -> None:
            try:
                result = op()
            except Exception as exc:  # noqa: BLE001
                wx.CallAfter(self._after_async_error, action_label, str(exc))
                return
            wx.CallAfter(self._after_async_done, action_label, result, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _after_async_done(
        self, action_label: str, result: CliResult, on_done: Callable[[CliResult], None]
    ) -> None:
        if self._closing:
            return
        if result.timed_out:
            self._live.announce(f"{action_label} timed out")
        elif result.success:
            self._live.announce(f"{action_label} succeeded")
        else:
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            detail = tail[-1] if tail else f"exit {result.returncode}"
            self._live.announce(f"{action_label} failed: {detail}")
        # Clear busy BEFORE calling on_done. If on_done spawns another async
        # op (e.g. _on_remove's done() calls _refresh_registered), that op's
        # _set_busy(True) takes effect cleanly without us clobbering it after
        # the fact (audit I-5).
        self._set_busy(False)
        on_done(result)

    def _after_async_error(self, action_label: str, message: str) -> None:
        if self._closing:
            return
        self._live.announce(f"{action_label} error: {message}")
        wx.MessageBox(
            f"{action_label} raised an unexpected error:\n\n{message}",
            "Marketplace error",
            wx.OK | wx.ICON_ERROR,
            self,
        )
        self._set_busy(False)

    # --------------------------------------------------------- handlers

    def _on_add(self, _evt: wx.CommandEvent) -> None:
        with _AddMarketplaceDialog(self) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            source = dlg.source
            scope = dlg.scope

        def op() -> CliResult:
            return self._cli.add_marketplace(source, scope=scope)

        def done(result: CliResult) -> None:
            if result.success:
                self._mark_dirty()
                self._refresh_registered()
            else:
                self._show_failure("Add marketplace", result)

        self._run_async(f"Adding marketplace {source}", op, done)

    def _on_remove(self, _evt: wx.CommandEvent) -> None:
        row = self._selected_row()
        if row is None:
            return
        if not row.registered:
            wx.MessageBox(
                f"Marketplace {row.name!r} is declared in plugins.json but is "
                f"not currently registered with the Claude CLI. Nothing to remove.",
                "Not registered",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        # Destructive: use WARNING icon and NO_DEFAULT so Enter doesn't
        # accidentally confirm (a11y review I-3, parity with main-frame's
        # _confirm_destructive).
        confirm = wx.MessageDialog(
            self,
            f"Remove marketplace {row.name!r} from the Claude CLI?\n"
            "This does not edit plugins.json.",
            "Confirm remove",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
        )
        try:
            if confirm.ShowModal() != wx.ID_YES:
                return
        finally:
            confirm.Destroy()

        def op() -> CliResult:
            return self._cli.remove_marketplace(row.name)

        def done(result: CliResult) -> None:
            if result.success:
                self._mark_dirty()
                self._refresh_registered()
            else:
                self._show_failure(f"Remove {row.name}", result)

        self._run_async(f"Removing marketplace {row.name}", op, done)

    def _on_update(self, _evt: wx.CommandEvent) -> None:
        row = self._selected_row()
        if row is None:
            return

        def op() -> CliResult:
            return self._cli.update_marketplace(row.name)

        def done(result: CliResult) -> None:
            if not result.success:
                self._show_failure(f"Update {row.name}", result)
            # Update doesn't change registration set; no refresh needed.

        self._run_async(f"Updating marketplace {row.name}", op, done)

    def _on_update_all(self, _evt: wx.CommandEvent) -> None:
        def op() -> CliResult:
            return self._cli.update_marketplace(None)

        def done(result: CliResult) -> None:
            if not result.success:
                self._show_failure("Update all marketplaces", result)

        self._run_async("Updating all marketplaces", op, done)

    def _on_refresh(self, _evt: wx.CommandEvent) -> None:
        self._refresh_registered()

    def _refresh_registered(self) -> None:
        self._set_busy(True)
        self._live.announce("Refreshing marketplace list…")

        def worker() -> None:
            try:
                names = self._cli.list_marketplaces()
            except Exception as exc:  # noqa: BLE001
                wx.CallAfter(self._after_async_error, "Refresh", str(exc))
                return
            wx.CallAfter(self._after_refresh_ok, names)

        threading.Thread(target=worker, daemon=True).start()

    def _after_refresh_ok(self, names: Optional[set[str]]) -> None:
        if self._closing:
            return
        if names is None:
            self._live.announce("Refresh failed")
            self._set_busy(False)
            return
        self._registered = set(names)
        self._populate_rows()
        self._live.announce(f"Refreshed: {len(self._registered)} registered")
        self._set_busy(False)

    def _show_failure(self, label: str, result: CliResult) -> None:
        msg = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        wx.MessageBox(
            f"{label} failed:\n\n{msg}",
            "Marketplace operation failed",
            wx.OK | wx.ICON_ERROR,
            self,
        )


class _AddMarketplaceDialog(wx.Dialog):
    """Sub-dialog: collect (source, scope) for ``claude plugin marketplace add``.

    The CLI accepts a ``<source>`` arg (URL, GitHub repo, or path) — the name
    is derived by the CLI itself from the marketplace's manifest, so we don't
    ask the user for it here.
    """

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, title="Add marketplace", size=(520, 220))
        self.SetMinSize((460, 200))

        panel = wx.Panel(self)

        self._source_label = wx.StaticText(
            panel,
            label=("&Source (URL, owner/repo, or local path):"),
        )
        self._source_input = wx.TextCtrl(panel, value="")
        self._source_input.SetName("Source")

        self._scope_label = wx.StaticText(panel, label="Scop&e:")
        self._scope_choice = wx.Choice(panel, choices=list(_SCOPES))
        self._scope_choice.SetSelection(0)
        self._scope_choice.SetName("Scope")

        self._btn_ok = wx.Button(panel, id=wx.ID_OK, label="&OK")
        self._btn_cancel = wx.Button(panel, id=wx.ID_CANCEL, label="&Cancel")
        self._btn_ok.SetDefault()
        self._btn_ok.Disable()  # enables once Source is non-empty

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(self._source_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        outer.Add(self._source_input, 0, wx.EXPAND | wx.ALL, 8)
        scope_row = wx.BoxSizer(wx.HORIZONTAL)
        scope_row.Add(self._scope_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        scope_row.Add(self._scope_choice, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(scope_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        outer.AddStretchSpacer(1)

        btn_row = wx.StdDialogButtonSizer()
        btn_row.AddButton(self._btn_ok)
        btn_row.AddButton(self._btn_cancel)
        btn_row.Realize()
        outer.Add(btn_row, 0, wx.EXPAND | wx.ALL, 8)

        panel.SetSizer(outer)

        self._source_input.Bind(wx.EVT_TEXT, self._on_text)
        self._source_input.SetFocus()

    def _on_text(self, _evt: wx.CommandEvent) -> None:
        self._btn_ok.Enable(bool(self._source_input.GetValue().strip()))

    @property
    def source(self) -> str:
        return self._source_input.GetValue().strip()

    @property
    def scope(self) -> str:
        return self._scope_choice.GetStringSelection() or "user"
