"""Entry point: ``python -m cc_plugin_manager``.

Config-resolution order (first hit wins):

1. ``CC_PLUGIN_MANAGER_CONFIG`` env var, if set.
2. ``plugins.json`` in the current working directory (user customization
   placed next to the .exe or in the project root).
3. ``plugins.json`` bundled inside the PyInstaller package, when running
   from a frozen build. This makes the shipped .exe usable out of the box
   without forcing users to copy ``plugins.json`` next to it.
"""

from __future__ import annotations

import os
import sys

import wx

from .cli import ClaudeCli, CliNotFoundError
from .data import Config, ConfigError, load_config
from .ui.main_frame import MainFrame


CONFIG_ENV = "CC_PLUGIN_MANAGER_CONFIG"
CONFIG_FILE = "plugins.json"


def _resolve_config_path() -> str:
    """Pick the best ``plugins.json`` we can find.

    Resolution order: env var → CWD → PyInstaller bundle → CWD (fallback for
    a clean error message). The env-var override is only honored when it
    actually points to a file — a typo (or a stale path from a previous
    install) falls through to CWD rather than producing a misleading
    "config file not found: <weird path>" error (audit M-5).
    """
    env_override = (os.environ.get(CONFIG_ENV) or "").strip()
    if env_override and os.path.isfile(env_override):
        return env_override

    cwd_path = os.path.abspath(CONFIG_FILE)
    if os.path.isfile(cwd_path):
        return cwd_path

    # PyInstaller frozen bundle: data files land in ``sys._MEIPASS``.
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        bundled = os.path.join(bundle_dir, CONFIG_FILE)
        if os.path.isfile(bundled):
            return bundled

    # If the env override was set but invalid, return it so the user sees
    # the path they actually configured in the error message ("config file
    # not found: <env-var path>"); otherwise fall back to the CWD path.
    if env_override:
        return env_override
    return cwd_path


def main() -> int:
    app = wx.App(False)

    try:
        cli = ClaudeCli.discover()
    except CliNotFoundError as exc:
        wx.MessageBox(str(exc), "Claude CLI not found", wx.OK | wx.ICON_ERROR)
        return 1

    config_path = _resolve_config_path()
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        wx.MessageBox(
            f"{exc}\n\nThe app will open with an empty plugin list. "
            f"Fix the file and use File → Reload.",
            "plugins.json problem",
            wx.OK | wx.ICON_WARNING,
        )
        config = Config()

    frame = MainFrame(config=config, cli=cli)
    frame.Show()
    app.MainLoop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
