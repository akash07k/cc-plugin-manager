from cc_plugin_manager.cli import CliResult
from cc_plugin_manager.data import Config, Marketplace, Plugin, PluginStatus
from cc_plugin_manager.worker import (
    ActionKind,
    ExecutionWorker,
    MarketplaceAddOp,
    OpResultEvent,
    OpStatus,
    PluginOp,
    ProgressEvent,
    RunCompleteEvent,
    SkipOp,
    build_operations,
)


def test_action_kind_values():
    assert ActionKind.INSTALL.value == "install"
    assert ActionKind.UPDATE.value == "update"
    assert ActionKind.UNINSTALL.value == "uninstall"


def test_plugin_op_label():
    op = PluginOp(action=ActionKind.INSTALL, plugin=Plugin("context7"), scope="user")
    assert "install" in op.label.lower()
    assert "context7" in op.label


def test_marketplace_op_label():
    op = MarketplaceAddOp(name="m", source="o/r", scope="user")
    assert "marketplace" in op.label.lower()
    assert "m" in op.label


def test_skip_op_label():
    op = SkipOp(
        plugin=Plugin("x", "m"),
        reason="marketplace 'm' not registered",
    )
    assert "skip" in op.label.lower()
    assert "x" in op.label


def test_progress_event_fields():
    op = PluginOp(action=ActionKind.INSTALL, plugin=Plugin("x"), scope="user")
    evt = ProgressEvent(index=1, total=5, op=op)
    assert evt.index == 1
    assert evt.total == 5
    assert evt.op is op


def test_op_status_values():
    assert OpStatus.OK.value == "OK"
    assert OpStatus.FAIL.value == "FAIL"
    assert OpStatus.SKIP.value == "SKIP"
    assert OpStatus.TIMEOUT.value == "TIMEOUT"


def test_cmd_for_marketplace_add():
    from cc_plugin_manager.worker import cmd_for

    op = MarketplaceAddOp(name="m", source="o/r", scope="user")
    assert cmd_for(op, "claude") == [
        "claude",
        "plugin",
        "marketplace",
        "add",
        "o/r",
        "--scope",
        "user",
    ]


def test_cmd_for_plugin_op():
    from cc_plugin_manager.worker import cmd_for

    op = PluginOp(action=ActionKind.INSTALL, plugin=Plugin("x", "m"), scope="project")
    assert cmd_for(op, "claude") == [
        "claude",
        "plugin",
        "install",
        "x@m",
        "--scope",
        "project",
    ]


def test_cmd_for_marketplace_remove():
    from cc_plugin_manager.worker import MarketplaceRemoveOp, cmd_for

    op = MarketplaceRemoveOp(name="karpathy-skills")
    assert cmd_for(op, "claude") == ["claude", "plugin", "marketplace", "remove", "karpathy-skills"]


def test_cmd_for_marketplace_update_named():
    from cc_plugin_manager.worker import MarketplaceUpdateOp, cmd_for

    op = MarketplaceUpdateOp(name="superpowers-marketplace")
    assert cmd_for(op, "claude") == [
        "claude",
        "plugin",
        "marketplace",
        "update",
        "superpowers-marketplace",
    ]


def test_cmd_for_marketplace_update_all():
    from cc_plugin_manager.worker import MarketplaceUpdateOp, cmd_for

    op = MarketplaceUpdateOp(name=None)
    assert cmd_for(op, "claude") == ["claude", "plugin", "marketplace", "update"]


def test_marketplace_remove_op_label():
    from cc_plugin_manager.worker import MarketplaceRemoveOp

    op = MarketplaceRemoveOp(name="karpathy-skills")
    assert "remove" in op.label.lower()
    assert "karpathy-skills" in op.label


def test_marketplace_update_op_label_named_vs_all():
    from cc_plugin_manager.worker import MarketplaceUpdateOp

    assert "update" in MarketplaceUpdateOp(name="x").label.lower()
    assert "all" in MarketplaceUpdateOp(name=None).label.lower()


def test_worker_dispatches_marketplace_remove(monkeypatch):
    """Worker correctly routes MarketplaceRemoveOp to cli.remove_marketplace."""
    from cc_plugin_manager.worker import MarketplaceRemoveOp

    cli = FakeCli()
    # Add a remove method that records the call.
    calls: list[str] = []

    def remove(name):
        calls.append(name)
        return CliResult(
            cmd=["claude"], returncode=0, stdout="", stderr="", duration=0.0, timed_out=False
        )

    cli.remove_marketplace = remove  # type: ignore[attr-defined]
    ops = [MarketplaceRemoveOp(name="m1"), MarketplaceRemoveOp(name="m2")]
    cb, events = _drain([])
    ExecutionWorker(cli=cli, ops=ops, post_event=cb).run()
    assert calls == ["m1", "m2"]
    complete = next(e for e in events if isinstance(e, RunCompleteEvent))
    assert complete.succeeded == 2 and complete.failed == 0


def test_worker_dispatches_marketplace_update_named_and_all(monkeypatch):
    from cc_plugin_manager.worker import MarketplaceUpdateOp

    cli = FakeCli()
    calls: list[object] = []

    def update(name=None):
        calls.append(name)
        return CliResult(
            cmd=["claude"], returncode=0, stdout="", stderr="", duration=0.0, timed_out=False
        )

    cli.update_marketplace = update  # type: ignore[attr-defined]
    ops = [MarketplaceUpdateOp(name="x"), MarketplaceUpdateOp(name=None)]
    cb, events = _drain([])
    ExecutionWorker(cli=cli, ops=ops, post_event=cb).run()
    assert calls == ["x", None]


def test_run_complete_tallies():
    evt = RunCompleteEvent(succeeded=3, skipped=1, failed=2, cancelled=False)
    assert evt.total == 6
    assert evt.error is None


def _cfg():
    return Config(
        marketplaces=[
            Marketplace(name="plugins-official"),  # not addable
            Marketplace(name="affaan-m/x", source="affaan-m/x"),  # addable
        ],
        plugins=[],
    )


def test_build_ops_install_not_installed_plugin_with_addable_marketplace_absent():
    cfg = _cfg()
    plugin = Plugin("p", "affaan-m/x")
    ops = build_operations(
        action=ActionKind.INSTALL,
        scope="user",
        selected=[(plugin, PluginStatus.NOT_INSTALLED)],
        config=cfg,
        present_markets=set(),
    )
    assert [type(o) for o in ops] == [MarketplaceAddOp, PluginOp]
    assert ops[0].name == "affaan-m/x"
    assert ops[0].source == "affaan-m/x"


def test_build_ops_deduplicates_marketplace_add():
    cfg = _cfg()
    p1 = Plugin("a", "affaan-m/x")
    p2 = Plugin("b", "affaan-m/x")
    ops = build_operations(
        action=ActionKind.INSTALL,
        scope="user",
        selected=[(p1, PluginStatus.NOT_INSTALLED), (p2, PluginStatus.NOT_INSTALLED)],
        config=cfg,
        present_markets=set(),
    )
    adds = [o for o in ops if isinstance(o, MarketplaceAddOp)]
    assert len(adds) == 1


def test_build_ops_skips_marketplace_missing_plugins():
    cfg = _cfg()
    plugin = Plugin("orphan", "plugins-official")
    ops = build_operations(
        action=ActionKind.INSTALL,
        scope="user",
        selected=[(plugin, PluginStatus.MARKETPLACE_MISSING)],
        config=cfg,
        present_markets=set(),
    )
    assert len(ops) == 1
    assert isinstance(ops[0], SkipOp)
    assert "plugins-official" in ops[0].reason


def test_build_ops_no_add_when_marketplace_already_present():
    cfg = _cfg()
    plugin = Plugin("p", "affaan-m/x")
    ops = build_operations(
        action=ActionKind.INSTALL,
        scope="user",
        selected=[(plugin, PluginStatus.NOT_INSTALLED)],
        config=cfg,
        present_markets={"affaan-m/x"},
    )
    assert [type(o) for o in ops] == [PluginOp]


def test_build_ops_no_add_when_marketplace_not_addable_but_present():
    cfg = _cfg()
    plugin = Plugin("session-report", "plugins-official")
    ops = build_operations(
        action=ActionKind.INSTALL,
        scope="user",
        selected=[(plugin, PluginStatus.NOT_INSTALLED)],
        config=cfg,
        present_markets={"plugins-official"},
    )
    assert [type(o) for o in ops] == [PluginOp]


def test_build_ops_uninstall_preserves_plugin_op_action():
    cfg = _cfg()
    plugin = Plugin("p", "affaan-m/x")
    ops = build_operations(
        action=ActionKind.UNINSTALL,
        scope="user",
        selected=[(plugin, PluginStatus.INSTALLED)],
        config=cfg,
        present_markets={"affaan-m/x"},
    )
    assert [type(o) for o in ops] == [PluginOp]
    assert ops[0].action == ActionKind.UNINSTALL


def test_build_ops_preserves_selection_order():
    cfg = _cfg()
    a = Plugin("a", "affaan-m/x")
    b = Plugin("b", "affaan-m/x")
    c = Plugin("c", "affaan-m/x")
    ops = build_operations(
        action=ActionKind.INSTALL,
        scope="user",
        selected=[
            (a, PluginStatus.NOT_INSTALLED),
            (b, PluginStatus.NOT_INSTALLED),
            (c, PluginStatus.NOT_INSTALLED),
        ],
        config=cfg,
        present_markets={"affaan-m/x"},
    )
    names = [o.plugin.name for o in ops if isinstance(o, PluginOp)]
    assert names == ["a", "b", "c"]


class FakeCli:
    executable: str = "claude"

    def __init__(self, script=None):
        self.calls: list[tuple[str, tuple, dict]] = []
        # script: optional list of CliResults to return, one per call
        self.script = list(script) if script else []

    def _next_result(self, cmd):
        if self.script:
            return self.script.pop(0)
        return CliResult(
            cmd=cmd, returncode=0, stdout="ok", stderr="", duration=0.01, timed_out=False
        )

    def install(self, plugin, scope):
        cmd = ["claude", "plugin", "install", plugin.qualified_id, "--scope", scope]
        self.calls.append(("install", (plugin, scope), {}))
        return self._next_result(cmd)

    def update(self, plugin, scope):
        cmd = ["claude", "plugin", "update", plugin.qualified_id, "--scope", scope]
        self.calls.append(("update", (plugin, scope), {}))
        return self._next_result(cmd)

    def uninstall(self, plugin, scope):
        cmd = ["claude", "plugin", "uninstall", plugin.qualified_id, "--scope", scope]
        self.calls.append(("uninstall", (plugin, scope), {}))
        return self._next_result(cmd)

    def add_marketplace(self, source, scope="user"):
        cmd = ["claude", "plugin", "marketplace", "add", source, "--scope", scope]
        self.calls.append(("add_marketplace", (source,), {"scope": scope}))
        return self._next_result(cmd)


def _drain(events):
    # synchronous callback collector
    collected: list = []

    def cb(evt):
        collected.append(evt)

    return cb, collected


def test_worker_runs_ops_sequentially_and_posts_events():
    cli = FakeCli()
    ops = [
        MarketplaceAddOp(name="m", source="o/r"),
        PluginOp(action=ActionKind.INSTALL, plugin=Plugin("a"), scope="user"),
        PluginOp(action=ActionKind.INSTALL, plugin=Plugin("b"), scope="user"),
    ]
    cb, events = _drain([])
    w = ExecutionWorker(cli=cli, ops=ops, post_event=cb)
    w.run()  # synchronous execution via run() (not start())

    progress = [e for e in events if isinstance(e, ProgressEvent)]
    results = [e for e in events if isinstance(e, OpResultEvent)]
    complete = [e for e in events if isinstance(e, RunCompleteEvent)]

    assert [p.index for p in progress] == [1, 2, 3]
    assert [p.total for p in progress] == [3, 3, 3]
    assert len(results) == 3
    assert all(r.status == OpStatus.OK for r in results)
    assert complete[0].succeeded == 3
    assert complete[0].skipped == 0
    assert complete[0].failed == 0
    assert complete[0].cancelled is False


def test_worker_records_skipops_without_cli_calls():
    cli = FakeCli()
    ops = [SkipOp(plugin=Plugin("x", "m"), reason="nope")]
    cb, events = _drain([])
    ExecutionWorker(cli=cli, ops=ops, post_event=cb).run()
    assert cli.calls == []
    complete = [e for e in events if isinstance(e, RunCompleteEvent)][0]
    assert complete.skipped == 1
    results = [e for e in events if isinstance(e, OpResultEvent)]
    assert results[0].status == OpStatus.SKIP


def test_worker_records_cli_failure():
    cli = FakeCli(
        script=[
            CliResult(
                cmd=["claude", "plugin", "install", "a", "--scope", "user"],
                returncode=1,
                stdout="",
                stderr="boom",
                duration=0.01,
                timed_out=False,
            )
        ]
    )
    ops = [PluginOp(action=ActionKind.INSTALL, plugin=Plugin("a"), scope="user")]
    cb, events = _drain([])
    ExecutionWorker(cli=cli, ops=ops, post_event=cb).run()
    results = [e for e in events if isinstance(e, OpResultEvent)]
    assert results[0].status == OpStatus.FAIL
    complete = [e for e in events if isinstance(e, RunCompleteEvent)][0]
    assert complete.failed == 1


def test_worker_records_timeout():
    cli = FakeCli(
        script=[
            CliResult(
                cmd=["claude"],
                returncode=-1,
                stdout="",
                stderr="",
                duration=120.0,
                timed_out=True,
            )
        ]
    )
    ops = [PluginOp(action=ActionKind.INSTALL, plugin=Plugin("a"), scope="user")]
    cb, events = _drain([])
    ExecutionWorker(cli=cli, ops=ops, post_event=cb).run()
    results = [e for e in events if isinstance(e, OpResultEvent)]
    assert results[0].status == OpStatus.TIMEOUT
    complete = [e for e in events if isinstance(e, RunCompleteEvent)][0]
    assert complete.failed == 1


def test_worker_honors_cancel_flag():
    """Contract: when cancel is set before run(), exactly one op completes
    (the worker checks cancellation after each op, not before it)."""
    cli = FakeCli()
    ops = [
        PluginOp(action=ActionKind.INSTALL, plugin=Plugin("a"), scope="user"),
        PluginOp(action=ActionKind.INSTALL, plugin=Plugin("b"), scope="user"),
        PluginOp(action=ActionKind.INSTALL, plugin=Plugin("c"), scope="user"),
    ]
    cb, events = _drain([])
    w = ExecutionWorker(cli=cli, ops=ops, post_event=cb)
    w.cancel()
    w.run()
    complete = [e for e in events if isinstance(e, RunCompleteEvent)][0]
    assert complete.cancelled is True
    results = [e for e in events if isinstance(e, OpResultEvent)]
    # Exactly one op completes after pre-set cancel; the rest are not run.
    assert len(results) == 1
    assert results[0].status == OpStatus.OK
    assert complete.succeeded == 1
    assert complete.failed == 0
    assert complete.skipped == 0


def test_worker_swallows_post_event_exceptions():
    """A failing post_event callback (e.g. destroyed frame) must not crash
    the worker — the run still finishes and the final RunCompleteEvent is
    delivered (or attempted; here we count attempts)."""
    cli = FakeCli()
    ops = [
        PluginOp(action=ActionKind.INSTALL, plugin=Plugin("a"), scope="user"),
        PluginOp(action=ActionKind.INSTALL, plugin=Plugin("b"), scope="user"),
    ]

    attempts: list[object] = []

    def flaky(evt):
        attempts.append(evt)
        # Simulate a destroyed frame: every other call raises.
        if len(attempts) % 2 == 0:
            raise RuntimeError("wrapped C/C++ object has been deleted")

    w = ExecutionWorker(cli=cli, ops=ops, post_event=flaky)
    w.run()  # must not raise
    # Worker attempted to deliver progress + result for each op + final complete.
    # 2 ops × 2 events + 1 RunCompleteEvent = 5
    assert len(attempts) == 5
    assert isinstance(attempts[-1], RunCompleteEvent)


def test_worker_dispatches_update_and_uninstall_actions():
    cli = FakeCli()
    ops = [
        PluginOp(action=ActionKind.UPDATE, plugin=Plugin("a"), scope="user"),
        PluginOp(action=ActionKind.UNINSTALL, plugin=Plugin("b"), scope="user"),
    ]
    cb, events = _drain([])
    ExecutionWorker(cli=cli, ops=ops, post_event=cb).run()
    actions = [c[0] for c in cli.calls]
    assert actions == ["update", "uninstall"]


def test_worker_posts_run_event_before_result_for_each_op():
    cli = FakeCli()
    ops = [PluginOp(action=ActionKind.INSTALL, plugin=Plugin("a"), scope="user")]
    cb, events = _drain([])
    ExecutionWorker(cli=cli, ops=ops, post_event=cb).run()
    # Find the first OpResultEvent and ensure there's a ProgressEvent before it
    progress_events = [e for e in events if isinstance(e, ProgressEvent)]
    ok_results = [e for e in events if isinstance(e, OpResultEvent) and e.status == OpStatus.OK]
    assert len(progress_events) == 1
    assert len(ok_results) == 1
    assert events.index(progress_events[0]) < events.index(ok_results[0])


def test_worker_catches_unexpected_exception():
    class BrokenCli(FakeCli):
        def install(self, plugin, scope):
            raise RuntimeError("kaboom")

    cli = BrokenCli()
    ops = [PluginOp(action=ActionKind.INSTALL, plugin=Plugin("a"), scope="user")]
    cb, events = _drain([])
    ExecutionWorker(cli=cli, ops=ops, post_event=cb).run()
    complete = [e for e in events if isinstance(e, RunCompleteEvent)][0]
    assert complete.failed == 1
    results = [e for e in events if isinstance(e, OpResultEvent)]
    assert any(r.status == OpStatus.FAIL and "kaboom" in r.stderr for r in results)
