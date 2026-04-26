"""Accessible dialog for adding a plugin entry to ``plugins.json``.

Opened via File → Add &plugin… (no global accelerator — File-menu mnemonic
is enough). Lets users append a plugin to the curated list without hand-
editing the JSON file.

Workflow:

1. User picks a marketplace (from the declared set) — or "(no marketplace)"
   for a bare plugin entry.
2. Optionally clicks **Fetch &available plugins**, which calls the upstream
   manifest verifier in a background thread and populates the plugin
   ComboBox with the marketplace's published plugin names.
3. User picks (or types) a plugin name. OK enables when the name is non-empty.
4. On OK, the dialog returns the new ``Plugin`` to the caller (which writes
   it to ``plugins.json`` and reloads). The dialog itself is purely a form;
   it does not perform any I/O on the JSON file.

Accessibility patterns (mirrored from MarketplaceDialog):

- Mnemonics: &Marketplace (Alt+M), &Fetch (Alt+F), &Plugin (Alt+P),
  &OK (Alt+O), &Cancel (Alt+C). None collide.
- Focus on the Marketplace Choice when the dialog opens.
- Esc closes (built-in wxDialog behaviour via SetEscapeId).
- Background fetch never blocks the UI; live region announces start/done.
- OK button disables while fetching, re-enables when the form is valid.
"""

from __future__ import annotations

import threading
from typing import Optional

import wx

from ..data import Marketplace, Plugin
from ..manifest_verifier import UpstreamManifest, fetch_manifest_cached
from .live_region import LiveRegion


_NO_MARKETPLACE_LABEL = "(no marketplace — bare entry)"


class AddPluginDialog(wx.Dialog):
    """Single-plugin entry form. ``picked`` holds the result on OK."""

    def __init__(
        self,
        parent: wx.Window,
        *,
        declared_marketplaces: list[Marketplace],
        existing_plugin_ids: Optional[set[str]] = None,
    ) -> None:
        super().__init__(parent, title="Add plugin", size=(560, 360))
        self.SetMinSize((480, 320))
        self._declared = list(declared_marketplaces)
        self._existing = set(existing_plugin_ids or set())
        self._busy = False
        self._closing = False
        self.picked: Optional[Plugin] = None

        self._build_widgets()
        self._build_layout()
        self._bind_events()

        self._marketplace_choice.SetFocus()

    # --------------------------------------------------------- widgets

    def _build_widgets(self) -> None:
        panel = wx.Panel(self)
        self._panel = panel

        self._marketplace_label = wx.StaticText(panel, label="&Marketplace")
        choices = [_NO_MARKETPLACE_LABEL] + [m.name for m in self._declared]
        self._marketplace_choice = wx.Choice(panel, choices=choices)
        self._marketplace_choice.SetSelection(0)
        self._marketplace_choice.SetName("Marketplace")

        self._btn_fetch = wx.Button(panel, label="&Fetch available plugins")
        self._btn_fetch.SetToolTip(
            "Fetch the marketplace's published plugin list from upstream "
            "(GitHub repos only). Populates the Plugin field below."
        )

        self._plugin_label = wx.StaticText(panel, label="&Plugin name")
        # ComboBox so users can either pick from the fetched list OR type
        # any name (e.g. for marketplaces we can't auto-verify).
        self._plugin_input = wx.ComboBox(panel, value="", choices=[], style=wx.CB_DROPDOWN)
        self._plugin_input.SetName("Plugin name")

        self._live = LiveRegion(panel, label="Ready")

        self._btn_ok = wx.Button(panel, id=wx.ID_OK, label="&OK")
        self._btn_cancel = wx.Button(panel, id=wx.ID_CANCEL, label="&Cancel")
        self._btn_ok.SetDefault()

        self._update_button_state()

    def _build_layout(self) -> None:
        panel = self._panel

        outer = wx.BoxSizer(wx.VERTICAL)

        # Marketplace row: StaticText then Choice (UIA LabeledBy).
        mkt_row = wx.BoxSizer(wx.HORIZONTAL)
        mkt_row.Add(self._marketplace_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        mkt_row.Add(self._marketplace_choice, 1, wx.EXPAND)
        outer.Add(mkt_row, 0, wx.EXPAND | wx.ALL, 8)

        # Fetch button on its own row, right-aligned.
        fetch_row = wx.BoxSizer(wx.HORIZONTAL)
        fetch_row.AddStretchSpacer(1)
        fetch_row.Add(self._btn_fetch, 0)
        outer.Add(fetch_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # Plugin label + ComboBox.
        outer.Add(self._plugin_label, 0, wx.LEFT | wx.RIGHT, 8)
        outer.Add(self._plugin_input, 0, wx.EXPAND | wx.ALL, 8)

        outer.AddStretchSpacer(1)

        outer.Add(self._live, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        btn_row = wx.StdDialogButtonSizer()
        btn_row.AddButton(self._btn_ok)
        btn_row.AddButton(self._btn_cancel)
        btn_row.Realize()
        outer.Add(btn_row, 0, wx.EXPAND | wx.ALL, 8)

        panel.SetSizer(outer)

        self.SetAffirmativeId(wx.ID_OK)
        self.SetEscapeId(wx.ID_CANCEL)

    def _bind_events(self) -> None:
        self._btn_fetch.Bind(wx.EVT_BUTTON, self._on_fetch)
        self._btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        # Cancel uses StdDialogButtonSizer + wx.ID_CANCEL — bind it explicitly
        # so we can flip _closing before EndModal fires (audit C-1; otherwise
        # an in-flight fetch's CallAfter would hit a destroyed widget).
        self._btn_cancel.Bind(wx.EVT_BUTTON, self._on_cancel_clicked)
        self._marketplace_choice.Bind(wx.EVT_CHOICE, self._on_marketplace_changed)
        self._plugin_input.Bind(wx.EVT_TEXT, self._on_plugin_text)
        self._plugin_input.Bind(wx.EVT_COMBOBOX, self._on_plugin_text)
        self.Bind(wx.EVT_CLOSE, self._on_dialog_close)

    def _on_cancel_clicked(self, _evt: wx.CommandEvent) -> None:
        self._closing = True
        self.EndModal(wx.ID_CANCEL)

    # --------------------------------------------------------- helpers

    def _on_dialog_close(self, evt: wx.CloseEvent) -> None:
        self._closing = True
        evt.Skip()

    def _selected_marketplace(self) -> Optional[Marketplace]:
        idx = self._marketplace_choice.GetSelection()
        if idx <= 0:  # 0 == "(no marketplace)"
            return None
        market_name = self._marketplace_choice.GetStringSelection()
        for m in self._declared:
            if m.name == market_name:
                return m
        return None

    def _on_marketplace_changed(self, _evt: wx.CommandEvent) -> None:
        # When the marketplace changes, the fetched plugin list (if any) no
        # longer applies; clear it so the user re-fetches deliberately.
        self._plugin_input.SetItems([])
        self._update_button_state()

    def _on_plugin_text(self, _evt: wx.CommandEvent) -> None:
        self._update_button_state()

    def _update_button_state(self) -> None:
        plugin_text = self._plugin_input.GetValue().strip()
        market = self._selected_marketplace()
        # Fetch is only useful when a marketplace is selected.
        self._btn_fetch.Enable(market is not None and not self._busy)
        # OK requires non-empty plugin name AND not currently fetching.
        self._btn_ok.Enable(bool(plugin_text) and not self._busy)

    # --------------------------------------------------------- fetch

    def _on_fetch(self, _evt: wx.CommandEvent) -> None:
        market = self._selected_marketplace()
        if market is None or market.source is None:
            return
        self._busy = True
        # Park focus on the Marketplace Choice (not the dialog's Cancel) so
        # an absent-minded Enter / Space press during the brief fetch window
        # doesn't accidentally close the whole dialog (a11y review I-1).
        # The dialog's Cancel is an "exit dialog" affordance; pressing it
        # while a fetch is in flight would surprise the user.
        if self._btn_fetch.HasFocus() or self._btn_ok.HasFocus():
            self._marketplace_choice.SetFocus()
        self._update_button_state()
        self._live.announce(f"Fetching plugins from {market.source}…")

        def worker() -> None:
            manifest = fetch_manifest_cached(market.source)
            wx.CallAfter(self._on_fetch_done, market, manifest)

        threading.Thread(target=worker, daemon=True).start()

    def _on_fetch_done(self, market: Marketplace, manifest: Optional[UpstreamManifest]) -> None:
        if self._closing:
            return
        self._busy = False
        if manifest is None:
            self._live.announce(
                f"Could not fetch manifest for {market.source} (non-GitHub source or offline)"
            )
            self._update_button_state()
            return
        self._plugin_input.SetItems(list(manifest.plugin_names))
        self._live.announce(
            f"Fetched {len(manifest.plugin_names)} plugin"
            f"{'s' if len(manifest.plugin_names) != 1 else ''} "
            f"from {manifest.name}"
        )
        # Hand focus to the plugin ComboBox so the keyboard user can pick
        # immediately without an extra Tab.
        self._plugin_input.SetFocus()
        self._update_button_state()

    # --------------------------------------------------------- ok

    def _on_ok(self, evt: wx.CommandEvent) -> None:
        plugin_text = self._plugin_input.GetValue().strip()
        if not plugin_text:
            return
        market = self._selected_marketplace()
        new_plugin = Plugin(name=plugin_text, marketplace=market.name if market else None)

        # Reject duplicates so the user gets a clear error rather than a
        # silent no-op when the dedup rule kicks in at load time.
        if new_plugin.qualified_id in self._existing:
            wx.MessageBox(
                f"{new_plugin.qualified_id} is already in plugins.json.",
                "Duplicate plugin",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return

        self.picked = new_plugin
        # Set _closing before letting StdDialog dismiss (audit C-1).
        self._closing = True
        evt.Skip()  # let StdDialog close with ID_OK
