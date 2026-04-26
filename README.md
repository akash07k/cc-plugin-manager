# Claude Code Plugin Manager

[![CI](https://github.com/akash07k/cc-plugin-manager/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/akash07k/cc-plugin-manager/actions/workflows/ci.yml)
[![Release](https://github.com/akash07k/cc-plugin-manager/actions/workflows/release.yml/badge.svg)](https://github.com/akash07k/cc-plugin-manager/actions/workflows/release.yml)
[![Latest release](https://img.shields.io/github/v/release/akash07k/cc-plugin-manager?display_name=tag&sort=semver)](https://github.com/akash07k/cc-plugin-manager/releases/latest)
[![Run release workflow](https://img.shields.io/badge/Actions-Run%20release-2ea44f?logo=github)](https://github.com/akash07k/cc-plugin-manager/actions/workflows/release.yml)

> The **Run release** badge above takes you to the workflow page; click
> **Run workflow** there, pick a `v*` tag from the **Use workflow from**
> dropdown, and confirm. See [Releasing](#releasing) for the full flow.

An accessible wxPython desktop GUI for bulk-managing
[Claude Code](https://claude.com/claude-code) plugins. Select plugins from a
curated list and install, update, or uninstall them in one run — without
memorising `claude plugin ...` subcommands or scope flags.

Designed from day one for full keyboard control and Windows screen readers
(NVDA, Narrator, JAWS).

## Features

- **Bulk operations** — pick many plugins, run one action (install / update /
  uninstall) at the selected scope (`user`, `project`, `local`, or `managed`
  for update).
- **Automatic marketplace setup** — if a plugin references a marketplace that
  isn't yet added, the manager adds it for you before running the operation.
- **Live status** — plugins are shown as installed / not installed based on
  `claude plugin list --json`, refreshed on launch and after each run.
- **Marketplace and Status filters** — narrow the list to one marketplace, or
  to plugins with a specific status (installed / not installed / marketplace
  missing / unknown), or compose both for fast triage.
- **Marketplace management dialog** (File → Marketplaces… or `Ctrl+M`) — view
  the union of declared (plugins.json) and registered (CLI) marketplaces with
  drift visible in the Status column; add new ones, remove, or update one /
  all. Long-running CLI calls run on a background thread so the dialog stays
  responsive.
- **Add plugin dialog** (File → Add plugin…) — pick a marketplace, optionally
  click "Fetch available plugins" to populate the plugin choices from the
  marketplace's upstream manifest, pick (or type) a name, click OK. The new
  entry is appended to `plugins.json` (alphabetized rewrite) and the main
  view refreshes.
- **Background manifest verification** — at startup, the app verifies each
  declared marketplace's `name` against the upstream `marketplace.json:name`
  on a best-effort basis. Mismatches are logged with a clear "plugins.json
  declares X but upstream publishes Y" message and announced to screen
  readers. Network failures are silent (offline-friendly). Cached for 24h.
- **Validated config** — `plugins.json` is checked at load time. Plugins that
  reference an undeclared marketplace fail loud at startup, not silently at
  status-derivation time.
- **Advanced bulk operations** (Advanced menu) — Update all installed plugins,
  Uninstall all installed plugins, Update all marketplaces, Remove all
  marketplaces, Reset everything. Each shows a confirmation dialog with
  counts and scope; all flow through the same execution pipeline so progress,
  cancel (Esc), and log work identically to a regular Execute.
- **Atomic config writes** — Add Plugin and any other programmatic edit to
  `plugins.json` use a temp-file + atomic rename, so a crash mid-write can
  never leave the user's curated list truncated.
- **Accessible by design** — native `wx.ListCtrl` (report mode) so every row
  announces its selection state; live-region announcements for milestones and
  failures (no per-op flooding); focus returns to a safe control after each
  run; summary dialog after completion.
- **No console noise** — `run.bat` launches the app with `pythonw` so
  double-clicking it doesn't leave a terminal window behind.

## Requirements

- Python 3.11 or later
- [Claude Code](https://claude.com/claude-code) CLI (`claude`) on your `PATH`
- Windows (primary target); macOS and Linux are best-effort

## Install

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.
Install uv once, then let it handle the rest:

```bash
uv sync --extra dev
```

That creates `.venv/`, installs wxPython plus the dev tooling, and pins
everything in `uv.lock`.

## Run

```bash
uv run python -m cc_plugin_manager
```

Or on Windows just double-click `run.bat` (verifies uv, syncs the venv on
first run, then launches via `pythonw`, no console window).

For a developer command runner with a menu, run `dev.bat` (interactive) or
`dev.bat <command>` (direct dispatch — see `dev.bat help` for the full list).

### Configuration

The manager reads plugin and marketplace definitions from `plugins.json` at
the repo root. You can point it at a different file with an environment
variable:

```bash
CC_PLUGIN_MANAGER_CONFIG=/path/to/my-plugins.json python -m cc_plugin_manager
```

A `plugins.json` entry may be a bare string (`"context7"`) or an object with
an explicit marketplace (`{"name": "session-report", "marketplace": "claude-plugins-official"}`).
IDs of the form `name@marketplace` are also accepted. Plugin entries that
reference a marketplace not declared in the `marketplaces` array are
rejected at load time.

When declaring a new marketplace, the `name` field must **match the upstream
repository's own `.claude-plugin/marketplace.json:name`**, not the repo name
or any nickname. The CLI registers marketplaces under the upstream-declared
name, so a mismatch silently surfaces every plugin in that marketplace as
"marketplace missing". For example, the repo `forrestchang/andrej-karpathy-skills`
publishes a marketplace named `karpathy-skills` — that's what goes in our
`plugins.json`, not the repo name. The app's startup verifier fetches each
declared marketplace's manifest and warns about any mismatch.

#### Subprocess timeouts

Per-action subprocess timeouts can be overridden with environment variables.
Each takes a non-negative float (seconds); invalid or unset values keep the
defaults so a typo never bricks the app:

| Action               | Env var                                  | Default |
|----------------------|------------------------------------------|---------|
| `claude plugin list` | `CC_PLUGIN_MANAGER_TIMEOUT_LIST`         | 30 s    |
| `install`            | `CC_PLUGIN_MANAGER_TIMEOUT_INSTALL`      | 600 s   |
| `update`             | `CC_PLUGIN_MANAGER_TIMEOUT_UPDATE`       | 600 s   |
| `uninstall`          | `CC_PLUGIN_MANAGER_TIMEOUT_UNINSTALL`    | 120 s   |
| `marketplace add/remove/update` | `CC_PLUGIN_MANAGER_TIMEOUT_MARKETPLACE` | 300 s |

## Keyboard and accessibility

- `Tab` / `Shift+Tab` cycle through focusable controls in logical reading
  order: Filter → Action → Scope → Plugin list → Select All → Deselect All →
  Refresh → Execute → Cancel → Log. The progress gauge and live-region label
  are not focusable (they're announced via live regions and the log).
- In the plugin list: arrow keys move focus (and selection, Explorer-style),
  `Shift+Arrow` extends selection, `Ctrl+Space` toggles a single row's
  selection without disturbing the rest, `Ctrl+A` selects all visible rows.
  Selection IS the chosen state — rows announce as "selected / not selected".
- `Alt+<letter>` activates the control whose label shows that underlined
  letter (e.g. `Alt+M` = Filter by Marketplace, `Alt+T` = Filter by Status,
  `Alt+X` = Execute, `Alt+G` = Log).
- `Ctrl+M` opens the Marketplaces dialog.
- `Escape` cancels a running operation.
- The status bar shows a persistent "N selected" counter (NVDA users can
  read it on demand with `NVDA+End`).
- A modal summary dialog announces the outcome (ok / warning / error) at the
  end of every run. Focus then returns to **Execute**.

See [`docs/a11y-smoke-test.md`](docs/a11y-smoke-test.md) for the full manual
screen-reader checklist used before releases.

## Develop

```bash
uv run pytest -q                                                    # unit tests
uv run ruff check .                                                 # lint
uv run mypy cc_plugin_manager/{data,cli,worker}.py                  # type-check (strict)
```

Or use `dev.bat` for an interactive menu / direct dispatch:

```bash
dev.bat                       # menu
dev.bat check                 # full CI gate (lint + format-check + types + tests)
dev.bat upgrade               # uv lock --upgrade then re-sync
dev.bat build                 # PyInstaller onedir build (default)
dev.bat build --onefile       # single .exe (slower startup, easier to ship)
```

The `build` extra (`uv sync --extra build`) pulls in PyInstaller so you don't
need it globally installed. Output lands in `dist/cc-plugin-manager/` (onedir)
or `dist/cc-plugin-manager.exe` (onefile); both are GUI-only (no console
window) and ship `plugins.json` as a bundled data file.

Project layout and conventions are documented in [`CLAUDE.md`](CLAUDE.md).

## Releasing

Releases are cut by [`.github/workflows/release.yml`](.github/workflows/release.yml)
on every pushed tag matching `v*`. The workflow runs all four CI gates,
builds two PyInstaller artifacts on `windows-latest`, and publishes a GitHub
release with assets attached — no manual asset uploading.

**Before tagging:**

1. Bump `[project].version` in `pyproject.toml` (the workflow refuses to
   publish if the tag and pyproject disagree).
2. Run `dev.bat check` locally to catch anything CI would reject.
3. Commit (`chore(release): v1.2.3`) and push to `main`.

**Cut a stable release** (replace `1.2.3` with your version):

```bash
git tag -s v1.2.3 -m "Release v1.2.3"
git push origin v1.2.3
```

> [!IMPORTANT]
> **Push the tag standalone, not bundled with a branch push.** GitHub
> sometimes consolidates a combined branch + tag push into a single event
> and silently drops the tag-trigger, leaving the workflow un-fired (and
> you get no email). Avoid `git push --follow-tags`,
> `git push origin main --tags`, and your IDE's "push with tags" button.
> Make sure `main` is already on `origin` *before* you run the two
> commands above. If a tag does end up un-triggered, see
> [Manual dispatch](#manual-dispatch-re-run-without-re-tagging) below.

Use `git tag -a` instead of `-s` if you don't sign tags. Watch the run in
the repo's **Actions** tab. On success the release appears under **Releases**
with three assets attached:

| Asset                                        | Description                            |
|----------------------------------------------|----------------------------------------|
| `cc-plugin-manager-vX.Y.Z-windows-x64.zip`   | Onedir build (faster startup)          |
| `cc-plugin-manager-vX.Y.Z-windows-x64.exe`   | Single-file build (slower cold start)  |
| `SHA256SUMS.txt`                             | SHA-256 checksums of both binaries     |

The release body is auto-generated from Conventional Commits via
[git-cliff](https://git-cliff.org/) and includes a SmartScreen advisory plus
verification instructions. After a stable release, `CHANGELOG.md` is
regenerated and committed back to `main` (best-effort — won't fail the
release if `main` is branch-protected).

**Cut a pre-release** — tags ending in `-rc.*`, `-beta.*`, or `-alpha.*` are
auto-marked as pre-release and stay off the repo's "Latest" badge:

```bash
git tag -s v1.2.3-rc.1 -m "Release candidate"
git push origin v1.2.3-rc.1
```

**If a tagged build fails:** the workflow refuses to publish if any gate
fails, so nothing is uploaded. Delete the tag, fix, and re-tag:

```bash
git push --delete origin v1.2.3   # remove the bad tag from the remote
git tag -d v1.2.3                 # remove the local tag
# fix the underlying issue, commit, then re-tag
```

### Manual dispatch (re-run without re-tagging)

The workflow also exposes a `workflow_dispatch` trigger, so you can
re-run a release for an existing tag without deleting and re-pushing it.
This is useful when a tag push didn't register a workflow run (a known
GitHub edge case if you push branch + tag together) or when you want to
re-attempt a failed release after a transient infra issue.

1. Click the **Run release** badge at the top of this README, or open
   `Actions → Release` directly.
2. Click **Run workflow**.
3. In the **Use workflow from** dropdown, pick the `v*` tag you want to
   release.
4. Click **Run workflow** to confirm.

The workflow refuses to run if you pick a branch (e.g. `main`) — the
tag-only guard keeps a stray click from accidentally cutting a release
off `HEAD`.

If you push branch + tag together (e.g. `git push --follow-tags`) and
the workflow doesn't fire, the cleanest fix is to push the tag again as
a standalone operation so GitHub records a clean tag-push event:

```bash
git push --delete origin v1.2.3
git push origin v1.2.3
```

### End-user install notes

The Windows binaries are **not code-signed**. SmartScreen and some antivirus
tools will warn on first launch — if you trust the source: right-click the
`.exe` → **Properties** → tick **Unblock** → **OK**, then launch normally.

Verify your download against `SHA256SUMS.txt`:

```powershell
Get-FileHash .\cc-plugin-manager-v1.2.3-windows-x64.exe -Algorithm SHA256
```

```bash
sha256sum -c SHA256SUMS.txt    # Git Bash / WSL
```

## License

[GNU Affero General Public License v3.0 only](LICENSE) (AGPL-3.0-only).

If you modify this software and run it as a network service, AGPL requires
you to make the modified source available to users of that service.
