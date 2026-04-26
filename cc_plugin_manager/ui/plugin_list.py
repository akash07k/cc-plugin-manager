"""Accessible plugin list backed by wx.ListCtrl (report mode, multi-select).

Why ListCtrl over CheckListBox and DataViewListCtrl:

* ``wx.dataview.DataViewListCtrl`` with a toggle renderer does not emit
  reliable UIA ``Toggle.ToggleState`` change events on Windows, so NVDA and
  Narrator stay silent when the user presses Space.
* ``wx.CheckListBox`` on Windows is drawn by wxWidgets rather than the
  native common control; its checkbox state changes are not consistently
  surfaced via UIA either.
* ``wx.ListCtrl`` in ``wx.LC_REPORT`` mode wraps the native ``SysListView32``
  common control. Selection *is* the state, and the control announces
  "selected / not selected" for every row through MSAA/UIA out of the box.

Keyboard behavior mirrors File Explorer:
  - Arrow keys move focus and, by default, the selection.
  - Ctrl+Arrow moves focus without changing selection.
  - Ctrl+Space toggles selection of the focused row.
  - Shift+Arrow extends the selection range.
  - Ctrl+A selects all rows (bound explicitly below for consistency across
    wxWidgets versions). Omitting ``event.Skip()`` on this path is
    deliberate — the native default varies, so we always handle it.

The control is a single tab stop; rows are reached via arrow keys.
"""

from __future__ import annotations

import wx

from ..data import Plugin, PluginStatus


class PluginListCtrl(wx.ListCtrl):
    COL_PLUGIN = 0
    COL_MARKETPLACE = 1
    COL_STATUS = 2

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES,
        )
        self.SetName("Plugins")
        # FromDIP scales widths under high-DPI displays. Without it, columns
        # render at 1× pixel widths even on 200% scaling.
        self.InsertColumn(self.COL_PLUGIN, "Plugin", width=self.FromDIP(260))
        self.InsertColumn(self.COL_MARKETPLACE, "Marketplace", width=self.FromDIP(220))
        self.InsertColumn(self.COL_STATUS, "Status", width=self.FromDIP(160))

        self._rows: list[Plugin] = []
        self._statuses: list[PluginStatus] = []

        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    # --------------------------------------------------------- public API

    def set_rows(
        self,
        plugins: list[Plugin],
        statuses: list[PluginStatus],
        *,
        preserve_selection: bool = True,
    ) -> None:
        """Replace the visible rows.

        When ``preserve_selection`` is true (the default), any plugins that
        were selected before the call **and** still appear in the new row set
        are reselected after rebuild. Without this, every refresh wipes the
        user's checked-set, which is a real UX problem after a successful
        install — the most common follow-up workflow is to update or
        uninstall the same selection.
        """
        assert len(plugins) == len(statuses)
        previously_selected = (
            {p.qualified_id for p, _ in self.checked_plugins()} if preserve_selection else set()
        )

        self.DeleteAllItems()
        self._rows = list(plugins)
        self._statuses = list(statuses)
        for i, (p, s) in enumerate(zip(plugins, statuses)):
            self.InsertItem(i, p.name)
            self.SetItem(i, self.COL_MARKETPLACE, p.marketplace or "")
            self.SetItem(i, self.COL_STATUS, s.value)

        if previously_selected:
            for i, p in enumerate(self._rows):
                if p.qualified_id in previously_selected:
                    self.SetItemState(i, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)

    def checked_plugins(self) -> list[tuple[Plugin, PluginStatus]]:
        """Return rows the user has chosen to act on.

        ``checked_plugins`` kept its original name so MainFrame did not need
        to change when we migrated from a check-box widget to selection-based
        semantics. Semantically these are "selected rows".
        """
        out: list[tuple[Plugin, PluginStatus]] = []
        idx = self.GetFirstSelected()
        while idx != -1:
            out.append((self._rows[idx], self._statuses[idx]))
            idx = self.GetNextSelected(idx)
        return out

    def set_all_checked(self, checked: bool) -> None:
        flag = wx.LIST_STATE_SELECTED if checked else 0
        # Freeze suppresses repaints during the bulk update; selection events
        # still fire, but UI flicker is gone and screen-reader announcements
        # don't run mid-transition.
        self.Freeze()
        try:
            for i in range(self.GetItemCount()):
                self.SetItemState(i, flag, wx.LIST_STATE_SELECTED)
        finally:
            self.Thaw()

    # --------------------------------------------------------- keyboard

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.ControlDown() and event.GetKeyCode() == ord("A"):
            self.set_all_checked(True)
            return  # deliberate: see module docstring
        event.Skip()
