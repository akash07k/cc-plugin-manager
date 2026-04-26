"""Headless-ish wxPython smoke tests.

These tests construct ``MainFrame`` with a stub CLI, drive a few public
operations on the pure helpers, and ensure import wiring stays clean.
They are skipped automatically on systems where wxPython cannot create an
``App`` instance (e.g., a Linux CI runner without DISPLAY).

The point is not to replicate manual NVDA/Narrator coverage — that's the job
of ``docs/a11y-smoke-test.md``. The point is to catch the trivial regressions
that escape unit tests: missing imports, attribute-rename drift, accidental
``raise`` paths in widget construction, etc.
"""

from __future__ import annotations

import pytest

wx = pytest.importorskip("wx")

from cc_plugin_manager.cli import Timeouts  # noqa: E402
from cc_plugin_manager.data import (  # noqa: E402
    Config,
    InstalledPlugin,
    Marketplace,
    Plugin,
    PluginStatus,
)


class _StubCli:
    """Minimal stand-in for ClaudeCli — no subprocess calls."""

    executable = "claude"
    timeouts = Timeouts()

    def list_plugins(self):
        return [InstalledPlugin(name="ctx", marketplace="m", scope="user", version="1")]

    def list_marketplaces(self):
        return {"m"}


@pytest.fixture(scope="module")
def wx_app():
    try:
        app = wx.App(False)
    except Exception as e:  # pragma: no cover — headless CI without display
        pytest.skip(f"wx.App could not initialize: {e}")
    yield app
    # No app.MainLoop(); we never enter the event loop. Frames created in
    # tests are explicitly Destroy()'d.


@pytest.fixture
def sample_config():
    return Config(
        marketplaces=[Marketplace(name="m", source="o/r")],
        plugins=[
            Plugin("a", "m"),
            Plugin("b", "m"),
            Plugin("ctx", None),
        ],
    )


def test_main_frame_constructs_without_errors(wx_app, sample_config):
    from cc_plugin_manager.ui.main_frame import MainFrame

    frame = MainFrame(config=sample_config, cli=_StubCli())
    try:
        # Walk a few invariants that tend to break under refactors.
        assert frame.GetTitle() == "Claude Code Plugin Manager"
        assert frame._filter_choices() == ["All", "m"]
        # Pure helpers
        plugins = list(sample_config.plugins)
        statuses = [PluginStatus.NOT_INSTALLED] * len(plugins)
        assert len(frame._apply_filter(plugins)) == 3
        assert len(frame._apply_filter_statuses(plugins, statuses)) == 3
    finally:
        frame.Destroy()


def test_filter_narrows_to_marketplace(wx_app, sample_config):
    from cc_plugin_manager.ui.main_frame import MainFrame

    frame = MainFrame(config=sample_config, cli=_StubCli())
    try:
        # Select "m" in the filter, then exercise filter helpers.
        frame._filter_choice.SetStringSelection("m")
        plugins = list(sample_config.plugins)
        statuses = [PluginStatus.NOT_INSTALLED] * len(plugins)
        assert [p.name for p in frame._apply_filter(plugins)] == ["a", "b"]
        assert len(frame._apply_filter_statuses(plugins, statuses)) == 2
    finally:
        frame.Destroy()


def test_filter_pair_combines_marketplace_and_status(wx_app, sample_config):
    """Both filters AND together; either set to 'All' disables that axis."""
    from cc_plugin_manager.ui.main_frame import MainFrame

    frame = MainFrame(config=sample_config, cli=_StubCli())
    try:
        plugins = list(sample_config.plugins)
        statuses = [
            PluginStatus.INSTALLED,  # a@m
            PluginStatus.NOT_INSTALLED,  # b@m
            PluginStatus.INSTALLED,  # ctx (no marketplace)
        ]

        # Default: All / All — pass-through.
        fp, fs = frame._filter_pair(plugins, statuses)
        assert [p.name for p in fp] == ["a", "b", "ctx"]
        assert fs == statuses

        # Status="installed" only.
        frame._status_filter_choice.SetStringSelection(PluginStatus.INSTALLED.value)
        fp, fs = frame._filter_pair(plugins, statuses)
        assert [p.name for p in fp] == ["a", "ctx"]
        assert fs == [PluginStatus.INSTALLED, PluginStatus.INSTALLED]

        # Marketplace="m" AND status="installed" → just "a".
        frame._filter_choice.SetStringSelection("m")
        fp, fs = frame._filter_pair(plugins, statuses)
        assert [p.name for p in fp] == ["a"]
        assert fs == [PluginStatus.INSTALLED]

        # Marketplace="m" AND status="not installed" → just "b".
        frame._status_filter_choice.SetStringSelection(PluginStatus.NOT_INSTALLED.value)
        fp, fs = frame._filter_pair(plugins, statuses)
        assert [p.name for p in fp] == ["b"]
        assert fs == [PluginStatus.NOT_INSTALLED]

        # Marketplace="m" AND status="marketplace missing" → empty.
        frame._status_filter_choice.SetStringSelection(PluginStatus.MARKETPLACE_MISSING.value)
        fp, fs = frame._filter_pair(plugins, statuses)
        assert fp == []
        assert fs == []
    finally:
        frame.Destroy()


def test_log_pane_appends_with_consistent_timestamp(wx_app):
    from cc_plugin_manager.ui.log_pane import LogPane

    frame = wx.Frame(None)
    try:
        log = LogPane(frame)
        log.append("RUN", "claude plugin install x")
        log.append_continuation("|", "ok")
        log.append_continuation("!", "warning")
        assert log.GetCount() == 3
        for i in range(3):
            assert log.GetString(i).startswith("[")  # timestamp prefix
    finally:
        frame.Destroy()


def test_plugin_list_preserves_selection_across_set_rows(wx_app):
    from cc_plugin_manager.ui.plugin_list import PluginListCtrl

    frame = wx.Frame(None)
    try:
        plist = PluginListCtrl(frame)
        plist.set_rows(
            [Plugin("a", "m"), Plugin("b", "m"), Plugin("c", "m")],
            [PluginStatus.NOT_INSTALLED] * 3,
        )
        # Select rows 0 and 2 (a and c).
        plist.SetItemState(0, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
        plist.SetItemState(2, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
        assert {p.name for p, _ in plist.checked_plugins()} == {"a", "c"}

        # Refresh with the same plugins; selection should survive.
        plist.set_rows(
            [Plugin("a", "m"), Plugin("b", "m"), Plugin("c", "m")],
            [PluginStatus.INSTALLED] * 3,
        )
        assert {p.name for p, _ in plist.checked_plugins()} == {"a", "c"}

        # Refresh with a DIFFERENT plugin set; the missing rows just drop
        # from selection.
        plist.set_rows(
            [Plugin("a", "m"), Plugin("d", "m")],
            [PluginStatus.INSTALLED, PluginStatus.NOT_INSTALLED],
        )
        assert [p.name for p, _ in plist.checked_plugins()] == ["a"]

        # preserve_selection=False wipes selection.
        plist.set_rows(
            [Plugin("a", "m"), Plugin("d", "m")],
            [PluginStatus.INSTALLED, PluginStatus.NOT_INSTALLED],
            preserve_selection=False,
        )
        assert plist.checked_plugins() == []
    finally:
        frame.Destroy()


def test_live_region_announce_does_not_raise(wx_app):
    from cc_plugin_manager.ui.live_region import LiveRegion

    frame = wx.Frame(None)
    try:
        live = LiveRegion(frame, label="Initial")
        # announce() must accept any string and never raise.
        live.announce("Hello")
        live.announce("World")  # coalesces with the prior call
    finally:
        frame.Destroy()


def test_result_label_covers_all_op_statuses():
    from cc_plugin_manager.ui.main_frame import _result_label
    from cc_plugin_manager.worker import OpStatus

    for status in OpStatus:
        # Must not raise — every defined status has a label.
        assert _result_label(status)


def test_marketplace_dialog_constructs_and_shows_union(wx_app):
    """Dialog opens, shows union(declared, registered), with status column."""
    from cc_plugin_manager.ui.marketplace_dialog import MarketplaceDialog

    declared = [
        Marketplace(name="declared-only", source=None),
        Marketplace(name="both", source="o/r"),
    ]
    registered = {"both", "registered-only"}
    parent = wx.Frame(None)
    try:
        dlg = MarketplaceDialog(parent, cli=_StubCli(), declared=declared, registered=registered)
        try:
            assert dlg.GetTitle() == "Marketplaces"
            # Three rows in the union, alphabetized.
            assert dlg._list.GetItemCount() == 3
            names = [dlg._list.GetItemText(i, 0) for i in range(3)]
            assert names == ["both", "declared-only", "registered-only"]
            # Status column reflects each row's set membership.
            statuses = [dlg._list.GetItemText(i, 2) for i in range(3)]
            # Plain-language status (a11y review I-4): more screen-reader-friendly
            # than the previous "declared, registered" / etc.
            assert "both" in statuses
            assert "plugins.json only" in statuses
            assert "CLI only" in statuses
            # Default selection lands on the first row, so Remove/Update enable.
            assert dlg._btn_remove.IsEnabled()
            assert dlg._btn_update.IsEnabled()
        finally:
            dlg.Destroy()
    finally:
        parent.Destroy()


def test_add_plugin_dialog_constructs_and_validates(wx_app):
    """Dialog opens, OK disables until plugin name is non-empty."""
    from cc_plugin_manager.ui.add_plugin_dialog import AddPluginDialog

    parent = wx.Frame(None)
    try:
        dlg = AddPluginDialog(
            parent,
            declared_marketplaces=[Marketplace(name="m", source="o/r")],
            existing_plugin_ids={"x"},
        )
        try:
            assert dlg.GetTitle() == "Add plugin"
            # Marketplace choice has the no-marketplace option plus declared.
            choices = [
                dlg._marketplace_choice.GetString(i)
                for i in range(dlg._marketplace_choice.GetCount())
            ]
            assert choices[0].startswith("(no marketplace")
            assert "m" in choices
            # OK starts disabled (empty plugin name).
            assert not dlg._btn_ok.IsEnabled()
            # Type a name → OK enables.
            dlg._plugin_input.SetValue("alpha")
            dlg._on_plugin_text(wx.CommandEvent())
            assert dlg._btn_ok.IsEnabled()
            # Picking a non-bare marketplace + a unique name should yield a
            # qualified Plugin on _on_ok. We simulate the OK path directly.
            dlg._marketplace_choice.SetStringSelection("m")
            dlg._plugin_input.SetValue("alpha")
            evt = wx.CommandEvent(wx.wxEVT_COMMAND_BUTTON_CLICKED, wx.ID_OK)
            dlg._on_ok(evt)
            assert dlg.picked is not None
            assert dlg.picked.qualified_id == "alpha@m"
        finally:
            dlg.Destroy()
    finally:
        parent.Destroy()


def test_add_plugin_dialog_rejects_duplicate(wx_app):
    """Adding an already-declared plugin shows an info dialog and does not set picked."""
    from cc_plugin_manager.ui.add_plugin_dialog import AddPluginDialog

    parent = wx.Frame(None)
    try:
        dlg = AddPluginDialog(
            parent,
            declared_marketplaces=[Marketplace(name="m", source="o/r")],
            existing_plugin_ids={"alpha@m"},
        )
        try:
            dlg._marketplace_choice.SetStringSelection("m")
            dlg._plugin_input.SetValue("alpha")
            evt = wx.CommandEvent(wx.wxEVT_COMMAND_BUTTON_CLICKED, wx.ID_OK)
            # MessageBox is modal — patch wx.MessageBox to a no-op for the test.
            import unittest.mock as _mock

            with _mock.patch("wx.MessageBox", return_value=wx.OK):
                dlg._on_ok(evt)
            assert dlg.picked is None  # rejected as duplicate
        finally:
            dlg.Destroy()
    finally:
        parent.Destroy()


def test_marketplace_dialog_marks_dirty_on_successful_op(wx_app):
    """When an async op succeeds, the dialog reports changed() == True."""
    from cc_plugin_manager.cli import CliResult
    from cc_plugin_manager.ui.marketplace_dialog import MarketplaceDialog

    parent = wx.Frame(None)
    try:
        dlg = MarketplaceDialog(parent, cli=_StubCli(), declared=[], registered={"x"})
        try:
            assert dlg.changed() is False
            # Simulate the async pipeline calling back with a successful result.
            ok_result = CliResult(
                cmd=["claude"],
                returncode=0,
                stdout="ok",
                stderr="",
                duration=0.0,
                timed_out=False,
            )
            called: list[CliResult] = []

            def on_done(r: CliResult) -> None:
                called.append(r)
                # In production, _on_add's done() marks dirty + refreshes;
                # mimic that side effect here.
                dlg._mark_dirty()

            dlg._after_async_done("Adding marketplace x", ok_result, on_done)
            assert called == [ok_result]
            assert dlg.changed() is True
        finally:
            dlg.Destroy()
    finally:
        parent.Destroy()
