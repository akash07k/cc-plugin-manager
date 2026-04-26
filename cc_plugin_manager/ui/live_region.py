"""Accessible live-region StaticText.

Wraps ``wx.StaticText`` and, after each label change, raises a UIA
``NameChange``-equivalent notification so Windows screen readers announce
it as a polite live region. Announcements are debounced so rapid updates
coalesce into a single utterance.

Reliability:

- We attempt to install a default ``wx.Accessible`` so ``GetAccessible()`` is
  not ``None`` on stock builds. If accessibility services aren't available
  (some non-Windows builds), the live region degrades to a plain label
  change — no crash.
- If a developer enables ``CC_PLUGIN_MANAGER_LIVE_REGION_DEBUG=1`` in the
  environment, missing-accessibility events are printed to stderr once so
  the failure is visible in the dev console.
"""

from __future__ import annotations

import os
import sys

import wx


class LiveRegion(wx.StaticText):
    """A StaticText that announces label changes to assistive technologies.

    Usage:
        live = LiveRegion(parent, label="Ready")
        live.announce("Installing 3 of 12: context7")
    """

    DEBOUNCE_MS = 200

    _missing_warning_logged = False

    def __init__(self, parent: wx.Window, label: str = "") -> None:
        super().__init__(parent, label=label)
        self.SetName("Status")
        self._pending: str | None = None
        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer, self._timer)
        self._install_default_accessible()

    def announce(self, text: str) -> None:
        """Queue an announcement. Coalesces with any pending one within DEBOUNCE_MS."""
        self._pending = text
        if not self._timer.IsRunning():
            self._timer.StartOnce(self.DEBOUNCE_MS)

    def _install_default_accessible(self) -> None:
        """Best-effort: ensure GetAccessible() returns a usable object."""
        try:
            accessible_cls = getattr(wx, "Accessible", None)
            if accessible_cls is None:
                return
            self.SetAccessible(accessible_cls(self))
        except Exception:
            pass

    def _on_timer(self, _event: wx.TimerEvent) -> None:
        if self._pending is None:
            return
        text = self._pending
        self._pending = None
        self.SetLabel(text)
        self._notify_accessibility(text)

    def _notify_accessibility(self, _text: str) -> None:
        try:
            accessible = self.GetAccessible()
        except Exception:
            accessible = None
        if accessible is None:
            self._warn_once(
                "LiveRegion: GetAccessible() returned None — announcements may be silent"
            )
            return
        try:
            event_id = getattr(wx, "wxACC_EVENT_OBJECT_NAMECHANGE", 0x800C)
            accessible.NotifyEvent(event_id, self, 0, 0)
        except Exception:
            self._warn_once("LiveRegion: NotifyEvent raised — announcements may be silent")

    @classmethod
    def _warn_once(cls, message: str) -> None:
        if cls._missing_warning_logged:
            return
        if os.environ.get("CC_PLUGIN_MANAGER_LIVE_REGION_DEBUG") == "1":
            print(message, file=sys.stderr)
        cls._missing_warning_logged = True
