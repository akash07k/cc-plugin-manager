# Accessibility Smoke Test

Run under **NVDA** and **Narrator** on Windows (and VoiceOver on macOS if available) before every release. All items must pass.

## Keyboard-only traversal

- [ ] Tab reaches: Filter by Marketplace → Filter by Status → Action → Scope → Plugin list → Select All → Deselect All → Refresh → Execute → Cancel → Log (in order). The progress gauge and the live-region label are not focusable; they're announced via the live region and reflected in the log pane.
- [ ] Shift+Tab walks backwards in the same order.
- [ ] No keyboard traps at any control.
- [ ] Every control is reached without mouse.

## Names, roles, states

- [ ] Filter by Marketplace reads as "Filter by Marketplace, combo box, All".
- [ ] Filter by Status reads as "Filter by Status, combo box, All". Choices: All, installed, not installed, marketplace missing, unknown.
- [ ] Action reads as "Action, grouping" with three radios labelled Install/Update/Uninstall.
- [ ] Scope reads the current value and is repopulated (not hidden) when Action changes.
- [ ] Plugin list announces each row with Plugin, Marketplace, Status, and selection state.
- [ ] **Ctrl+Space** on a row toggles its selection without changing the rest, announced as "selected" / "not selected". (Plain Spacebar in `LC_REPORT` mode replaces the selection — that's standard Windows multi-select behavior; do NOT test bare-Space toggling.)
- [ ] Ctrl+A in the plugin list selects every visible row.
- [ ] Execute / Cancel announce label + state ("unavailable" when disabled).

## Mnemonics (Alt+letter)

Verify each is unique at the panel level and reaches the expected control:

- [ ] Alt+M → Filter by **M**arketplace combo
- [ ] Alt+T → Filter by Sta**t**us combo
- [ ] Alt+A → **A**ction radio group
- [ ] Alt+S → **S**cope combo
- [ ] Alt+P → **P**lugins list (focuses list, not a row)
- [ ] Alt+L → Se**l**ect All button
- [ ] Alt+D → **D**eselect All button
- [ ] Alt+R → **R**efresh button
- [ ] Alt+X → E**x**ecute button
- [ ] Alt+C → **C**ancel button
- [ ] Alt+G → Lo**g** label (focuses the log pane via LabeledBy)

## Live-region behavior

Live announcements are deliberately throttled to milestones — flooding NVDA's speech queue is worse than under-announcing. Per-op detail goes to the log pane.

- [ ] Changing the marketplace filter announces "Marketplace <name>: N plugins".
- [ ] Changing the status filter announces "Status <value>: N plugins".
- [ ] The two filters compose (AND): selecting "everything-claude-code" plus "not installed" shows only not-installed plugins from that marketplace.
- [ ] Changing the Action announces "Action <verb>".
- [ ] Starting a run announces "Running N operations. Press Escape to cancel."
- [ ] During a run, every 5th operation is announced (e.g., "Installing 5 of 20: ..."), plus the first and last operations and any failures/timeouts. Successful per-op completions are NOT announced; check the log pane for them.
- [ ] Run completion announces "Done. X ok, Y failed, Z skipped."
- [ ] If `CC_PLUGIN_MANAGER_LIVE_REGION_DEBUG=1` is set in the environment, the dev console prints a one-time warning if `GetAccessible()` returns `None` on the live region (helps catch broken builds).

## Focus management

- [ ] On Execute click, focus moves to **Cancel** before any controls disable (so screen-reader users never land on a disabled widget).
- [ ] Escape cancels a running operation at any time.
- [ ] When the run completes, the summary dialog appears modally; on dismissal, focus returns to **Execute**, and the post-run refresh starts after the dialog closes.
- [ ] New log entries do NOT steal focus.
- [ ] Closing the window during a run prompts "Cancel and exit?" — choosing Yes briefly waits for the worker to drain, then closes; choosing No vetoes the close.

## Log pane

- [ ] Arrow keys move between entries; each entry announces as a single unit.
- [ ] Every entry begins with a `[HH:MM:SS]` timestamp; continuation lines from CLI output use `|` (stdout) or `!` (stderr) markers.
- [ ] Ctrl+C copies the selected entry to the clipboard.
- [ ] Ctrl+Shift+C copies the entire log.

## Visual-free signals

- [ ] Status values ("installed", "not installed", "marketplace missing", "unknown") are textual, not color-coded.
- [ ] No information is conveyed by color alone anywhere.

## Mid-run control state

- [ ] Both filters (Marketplace, Status), Action, Scope, plugin list, Select All, Deselect All, Refresh, Reload (menu), and Execute are all disabled during a run.
- [ ] Cancel and the Log remain reachable during a run.
- [ ] All disabled controls are re-enabled on run completion.

## High contrast & DPI

- [ ] In Windows High Contrast mode, every control remains visible and distinguishable.
- [ ] Selection state in the plugin list is visible (not just a color difference).
- [ ] Plugin-list column widths scale at 150%/200% DPI (they use `FromDIP`).

## Status bar

- [ ] Status bar field 0 ("Ready" → "<X> installed / <Y> marketplaces" → "Done. ...") updates over the session.
- [ ] Status bar field 1 carries a persistent "N selected" counter. NVDA users can read it on demand with **NVDA+End**; it is not auto-announced on every selection change (that would be spam).

## Refresh behavior

- [ ] Mashing the Refresh button or letting Refresh re-fire after a run produces only one announcement and one log line per round; older in-flight refreshes are coalesced and discarded.
- [ ] Refresh button disables while a refresh is in flight, re-enables on completion.
- [ ] Selection survives a successful refresh (typical workflow: install → refresh → uninstall the same set).

## Advanced menu (destructive bulk operations)

- [ ] Alt+V opens the Advanced menu. Within it: Alt+U Update all installed plugins, Alt+N Uninstall all installed plugins…, Alt+A Update all marketplaces, Alt+R Remove all marketplaces…, Alt+E Reset everything…
- [ ] Each destructive item opens a YES/NO/WARNING dialog. Default focus is on **No** (NVDA announces "No button, default"). Pressing Enter dismisses without action; pressing Y confirms and starts a run.
- [ ] During an Advanced-menu run, all five Advanced items, Reload, Add plugin, Marketplaces, Execute, plugin list, both filters, Action, and Scope are disabled. Cancel and Log remain reachable.
- [ ] Esc during the run cancels exactly as it does for normal Execute.
- [ ] **Run-kind announce**: starting an Advanced run announces a kind-specific message ("Uninstalling all 30 plugins from scope user. Press Escape to cancel.") rather than the generic "Running N operations".
- [ ] **Time-based announce fallback**: even when the per-Nth stride is silent, no more than ~8 seconds passes without a live-region update. (Force this by uninstalling a small set with a slow CLI; verify periodic announcements.)
- [ ] **Reset Everything** against a populated state: confirmation title includes the scope ("Reset everything in scope 'user'"). Body is sentence-per-line. Final summary distinguishes plugin from marketplace results, e.g. "Plugins: 12 ok, 0 failed, 0 skipped. Marketplaces: 4 ok, 0 failed."
- [ ] Reset Everything against an empty state shows "Already empty" info dialog; no run starts.
- [ ] Summary dialog at run end places focus on Execute after dismissal, even when the run was started from the Advanced menu.

## Add plugin dialog (File → Add plugin…)

- [ ] Dialog opens; focus lands on the **Marketplace** Choice (Alt+M).
- [ ] Marketplace Choice lists "(no marketplace — bare entry)" first, then declared marketplaces.
- [ ] **Fetch available plugins** (Alt+F) is enabled only when a real marketplace is selected; clicking it announces "Fetching plugins from <source>…" then "Fetched N plugins from <name>" once complete. Network failure announces "Could not fetch manifest…" and the dialog stays usable.
- [ ] After a successful fetch, focus moves to the **Plugin name** ComboBox (Alt+P) so the user can pick without an extra Tab.
- [ ] **OK** (Alt+O) is disabled until Plugin name is non-empty.
- [ ] Picking a duplicate plugin (already in plugins.json) shows an info MessageBox; OK doesn't dismiss the dialog.
- [ ] **Cancel** (Alt+C) and Esc both close the dialog without writes.
- [ ] After successful Add: log line "added <id> to plugins.json", live-region announce "Added <id>", main list refreshes with the new entry visible.

## Background manifest verification (post-launch)

- [ ] Within ~5 seconds of launch, if any declared marketplace's `name` doesn't match its upstream `marketplace.json:name`, a WARN log entry appears: "manifest mismatch: plugins.json declares X but upstream Y publishes name Z".
- [ ] The live region announces "N marketplace name mismatches — see log" (singular form for N=1).
- [ ] Marketplaces with non-GitHub sources (or when offline) get a single INFO line stating that auto-verify was skipped — no errors, no failures, no announce.

## Marketplaces dialog (File → Marketplaces… or Ctrl+M)

- [ ] Dialog opens; focus lands on the marketplace list (announced as "Marketplaces, list").
- [ ] List shows the union of declared (plugins.json) and registered (CLI) marketplaces.
- [ ] Each row's Status column reads as one of: "declared, registered" / "declared, not registered" / "registered (not in plugins.json)".
- [ ] Buttons reachable via Tab AND via unique mnemonic. Verify each:
    - Alt+A → **A**dd...
    - Alt+R → **R**emove
    - Alt+U → **U**pdate
    - Alt+L → Update A**l**l (deliberately L to avoid A collision with Add)
    - Alt+F → Re**f**resh
    - Alt+C → **C**lose
- [ ] Add… opens a sub-dialog with **S**ource (Alt+S) and Scop**e** (Alt+E) fields. OK is disabled until Source is non-empty.
- [ ] Successful Add / Remove / Update announces e.g. "Adding marketplace owner/repo…" then "Adding marketplace owner/repo succeeded" via the dialog's live region.
- [ ] Failed CLI ops show a MessageBox with stderr; focus returns to the dialog after dismissal.
- [ ] During a CLI op, action buttons disable; if focus was on a disabled-target button it moves to Close.
- [ ] Esc dismisses the dialog at any time.
- [ ] After the dialog closes, if anything was changed, the main frame logs "marketplaces changed; refreshing" and re-queries the CLI.
- [ ] Ctrl+M is disabled mid-run (the menu item is greyed out, accelerator is a no-op).

## Golden paths (keyboard only)

- [ ] Select one plugin → Install (scope user) → completes → refresh shows "installed".
- [ ] Select installed plugin → Uninstall (scope user) → completes → refresh shows "not installed".
- [ ] Select one plugin → Update (scope user) → completes with no error.
- [ ] Select plugin whose marketplace is not addable → Execute → row logged as SKIP with clear reason.
- [ ] Select 12 plugins → Install → live region announces ops 1, 5, 10, 12 (not 2/3/4/6/7/8/9/11) and any failures.
