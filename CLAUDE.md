# cc-plugin-manager

Accessible wxPython desktop app that drives the `claude` CLI to manage Claude
Code plugins and marketplaces in bulk. Accessibility (keyboard + Windows
screen readers) is a first-class, non-negotiable requirement.

## Stack

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management and task running
  (lockfile is `uv.lock`; do NOT use bare `pip install -e .`)
- wxPython 4.2+ (GUI)
- pytest (tests), ruff (lint + format), mypy strict on `data`, `cli`, `worker`

## Layout

```
cc_plugin_manager/
  __main__.py             # `python -m cc_plugin_manager` entry point
  data.py                 # Plugin/Marketplace/Config dataclasses, derive_status,
                          # load_config, write_config (atomic)
  cli.py                  # ClaudeCli wrapper around the `claude` executable
  worker.py               # ExecutionWorker + Operation tagged union
                          # (PluginOp / MarketplaceAddOp / MarketplaceRemoveOp /
                          # MarketplaceUpdateOp / SkipOp)
  manifest_verifier.py    # Best-effort fetch of upstream marketplace.json
                          # (GitHub raw, 24h cache, stdlib-only)
  ui/
    main_frame.py         # top-level wx.Frame (menu, run flow, refresh)
    plugin_list.py        # wx.ListCtrl in LC_REPORT mode (see note below)
    log_pane.py           # wx.ListBox log with Ctrl+C / Ctrl+Shift+C copy
    live_region.py        # StaticText-based ARIA-live equivalent
    marketplace_dialog.py # File → Marketplaces… (Ctrl+M)
    add_plugin_dialog.py  # File → Add plugin…
plugins.json              # seed config (canonical-format, alphabetized)
pyproject.toml            # project metadata, deps, optional [build] extras
uv.lock                   # resolved dep versions (committed)
scripts/
  launch.py               # PyInstaller absolute-import wrapper
  build_exe.py            # PyInstaller invocation (onedir / --onefile)
run.bat                   # uv-aware pythonw launcher (no console window)
dev.bat                   # developer command runner (menu + direct dispatch)
tests/                    # pytest suite (~144 tests; pure logic + UI smoke)
docs/                     # a11y smoke-test checklist
```

## Run / test / lint

```
uv sync --extra dev                                                 # one-time install
uv run pytest -q                                                    # tests
uv run ruff check                                                   # lint (must pass)
uv run ruff format --check .                                        # format gate
uv run mypy cc_plugin_manager/{data,cli,worker}.py                  # type-check (strict)
uv run python -m cc_plugin_manager                                  # launch GUI
```

Or use `dev.bat` (interactive menu) or `dev.bat <command>` (direct dispatch —
`install`, `test`, `cov`, `lint`, `format`, `format-check`, `types`, `check`,
`build [--onefile]`, `clean`, `run`, `version`, `upgrade`).

`dev.bat check` runs all four CI gates locally and is the canonical "pre-commit"
command.

## Configuration

Override config path with `CC_PLUGIN_MANAGER_CONFIG=/path/to/plugins.json`.
If the env var points to a non-existent file, the loader falls back to CWD
then to the PyInstaller-bundled default.

Per-action subprocess timeouts can be overridden via these env vars (each
takes a non-negative float in seconds, invalid values are silently ignored):

```
CC_PLUGIN_MANAGER_TIMEOUT_LIST          # default 30
CC_PLUGIN_MANAGER_TIMEOUT_INSTALL       # default 600
CC_PLUGIN_MANAGER_TIMEOUT_UPDATE        # default 600
CC_PLUGIN_MANAGER_TIMEOUT_UNINSTALL     # default 120
CC_PLUGIN_MANAGER_TIMEOUT_MARKETPLACE   # default 300 (add / remove / update)
```

## Conventions

- **Shell is Windows git-bash**, not PowerShell. Use POSIX paths and redirects
  (`/dev/null`, not `NUL`).
- **`.bat` files use CRLF line endings.** The `Write` tool emits LF; convert
  every `.bat` rewrite back to CRLF or `cmd.exe` will fail to find batch labels.
- **Commits:** Conventional Commits (`feat(ui):`, `fix:`, `chore:`, `docs:`,
  `style:`). Commits are GPG-signed (`-S`). Keep them small and focused — one
  logical change per commit.
- **Before committing:** `dev.bat check` must pass. Or run the four gates
  individually: pytest, ruff check, ruff format --check, mypy.

## Design constraints worth preserving

These are non-obvious decisions documented in code that should not regress.

- **Accessibility is the feature.** Every control must announce its name,
  role, and state to Windows screen readers (NVDA, Narrator). Any UI change
  must keep: full keyboard operability, logical tab order, live-region
  announcements for async state, focus returning to a safe control after
  modal flows. The smoke-test checklist in `docs/a11y-smoke-test.md` is the
  pre-release gate.
- **`wx.ListCtrl` in `LC_REPORT` mode is deliberate.** Do NOT "modernize" it
  to `wx.dataview.DataViewListCtrl` or `wx.CheckListBox`:
  - DataView toggle renderers don't emit reliable UIA `Toggle.ToggleState`
    events on Windows — NVDA/Narrator stay silent.
  - CheckListBox is drawn by wxWidgets, not the native common control, so its
    checkbox changes aren't surfaced via UIA either.
  - ListCtrl wraps native `SysListView32`, which announces "selected / not
    selected" for every row out of the box. Selection *is* the checked state.
- **Live-region announcements are milestone-only during a run** (every 5th op
  + first + last + failures + skips). Per-op success would flood NVDA's
  speech queue (200 ms debounce replaces, doesn't aggregate). A time-based
  fallback (`_ANNOUNCE_FALLBACK_SECS = 8.0`) prevents long destructive runs
  from going silent. The log pane carries per-op detail for users who want it.
- **Worker `_post()` swallows post-time exceptions.** A failing event delivery
  (e.g. destroyed frame) must not crash the daemon thread. Combined with the
  `_closing` flag set from every dialog/frame dismissal path, this prevents
  use-after-destroy crashes.
- **`_closing` is set BEFORE `EndModal`/`Destroy`, not after.** EndModal does
  NOT fire `EVT_CLOSE`, so dialog button handlers must flip `_closing` directly
  before dismissal. Setting it BEFORE the user confirms a destructive close
  also corrupts run state (events get dropped while the user thinks).
- **`data.write_config` is atomic.** Writes go to a sibling tempfile then
  `os.replace` over the target. A crash mid-write cannot truncate the user's
  curated `plugins.json`.
- **Plugin IDs** accept both `name` and `name@marketplace` forms and are
  normalized in `data.normalize_plugin_id` (handles leading dashes, stray
  whitespace, wrong separators, rejects `@` in dict-form `name`).
- **Marketplace auto-add.** If a plugin references a marketplace not present
  in `claude plugin marketplace list --json`, the worker adds it (via
  `claude plugin marketplace add <source> --scope user`) before running the
  plugin operation. Sources come from `plugins.json`.
- **Marketplace `name` must match the upstream `marketplace.json` `name`.**
  When the CLI auto-adds a marketplace from a source, it registers it under
  the name declared in the source's own `.claude-plugin/marketplace.json`,
  NOT under whatever name we wrote in our `plugins.json`. If they differ,
  every plugin entry that references our (wrong) name silently surfaces as
  `MARKETPLACE_MISSING`. The startup manifest verifier (`manifest_verifier.py`)
  fetches each declared marketplace's manifest in the background and logs
  mismatches. Example: the repo `forrestchang/andrej-karpathy-skills` declares
  its marketplace as `karpathy-skills`, so our entry is
  `{"name": "karpathy-skills", "source": "forrestchang/andrej-karpathy-skills"}`
  even though the repo and plugin are named `andrej-karpathy-skills`.
- **Marketplace references are validated at load time.** `load_config` rejects
  plugin entries whose `marketplace` field doesn't appear in the declared
  marketplaces list — fail loud at startup, not silently at status time.
- **Real CLI shape.** `claude plugin list --json` returns entries with an
  `id` field of the form `name@marketplace` (not separate `name`/`marketplace`
  keys). `cli._parse_installed_id` handles both the real shape and the legacy
  test-fixture shape.
- **Refresh-thread coalescing.** `_refresh_gen` counter ensures only the
  latest in-flight refresh's result is applied; older threads' results are
  discarded. Refresh button is disabled while in-flight AND while a worker
  run is alive (so refresh-after-run doesn't open a CLI race).
- **`_installed_as_plugins` skips rows with `scope=None`.** A CLI version
  that omits scope reports through `claude plugin list --json` would
  otherwise be silently included in scoped bulk operations, leading to
  cross-scope destructive actions.

## Scopes

- `install` / `uninstall`: `user`, `project`, `local`
- `update`: `user`, `project`, `local`, `managed`
- `plugin marketplace add`: `user`, `project`, `local`
- `plugin marketplace remove` / `update`: no `--scope` flag

There is no `system` scope — do not add one.

## When in doubt

Read the code. This document stays short on purpose; the module docstrings in
`data.py`, `cli.py`, `worker.py`, `manifest_verifier.py`,
`ui/main_frame.py`, `ui/plugin_list.py`, `ui/marketplace_dialog.py`, and
`ui/add_plugin_dialog.py` carry the rationale for non-obvious choices.


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
