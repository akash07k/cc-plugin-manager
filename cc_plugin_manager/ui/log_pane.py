"""wx.ListBox-based log pane.

One entry per event. Arrow-key navigable by screen readers. Never steals
focus on append. Ctrl+C copies the selected entry; Ctrl+Shift+C copies all.

Format: every entry is timestamped as ``[HH:MM:SS] LEVEL message``. Continuation
lines from multi-line CLI output use ``[HH:MM:SS]   | …`` and ``[HH:MM:SS]   ! …``
prefixes for stdout and stderr respectively, so screen-reader users can tell
where one operation's output ends and the next begins.
"""

from __future__ import annotations

from datetime import datetime

import wx


class LogPane(wx.ListBox):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, style=wx.LB_SINGLE | wx.LB_NEEDED_SB)
        self.SetName("Log")
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    # --------------------------------------------------------- public API

    def append(self, level: str, message: str) -> None:
        """Append a new entry. Does NOT change focus or selection."""
        self._append_raw(f"[{self._ts()}] {level} {message}")

    def append_continuation(self, marker: str, line: str) -> None:
        """Append a continuation line from CLI output.

        ``marker`` should be ``"|"`` for stdout, ``"!"`` for stderr (or any
        single char chosen by the caller). The line is prefixed so screen
        readers announce the marker explicitly.
        """
        self._append_raw(f"[{self._ts()}]   {marker} {line}")

    def clear(self) -> None:
        self.Clear()

    # ------------------------------------------------------------- helpers

    def _append_raw(self, line: str) -> None:
        self.Append(line)
        count = self.GetCount()
        if count > 0:
            self.EnsureVisible(count - 1)

    @staticmethod
    def _ts() -> str:
        return datetime.now().strftime("%H:%M:%S")

    # --------------------------------------------------------- keyboard

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.ControlDown() and event.GetKeyCode() == ord("C"):
            if event.ShiftDown():
                self._copy_all()
            else:
                self._copy_selected()
            return
        event.Skip()

    def _copy_selected(self) -> None:
        sel = self.GetSelection()
        if sel == wx.NOT_FOUND:
            return
        self._to_clipboard(self.GetString(sel))

    def _copy_all(self) -> None:
        lines = [self.GetString(i) for i in range(self.GetCount())]
        self._to_clipboard("\n".join(lines))

    @staticmethod
    def _to_clipboard(text: str) -> None:
        if not wx.TheClipboard.Open():
            return
        try:
            wx.TheClipboard.SetData(wx.TextDataObject(text))
        finally:
            wx.TheClipboard.Close()
