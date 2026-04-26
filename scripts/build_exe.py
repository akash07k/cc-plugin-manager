"""Build a standalone ``cc-plugin-manager.exe`` via PyInstaller.

Usage::

    uv run python scripts/build_exe.py             # onedir (faster startup)
    uv run python scripts/build_exe.py --onefile   # single .exe (slower startup)

Output lands in ``dist/cc-plugin-manager/`` (onedir) or
``dist/cc-plugin-manager.exe`` (onefile). The ``--windowed`` flag suppresses
the console window so double-clicking the .exe launches the GUI directly,
mirroring how ``run.bat`` invokes ``pythonw``.

``plugins.json`` is bundled as a data file so the launched .exe can read its
default config without the user having to provide one.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "cc-plugin-manager.spec"
# IMPORTANT: PyInstaller cannot directly bundle ``cc_plugin_manager/__main__.py``
# because it uses relative imports (``from .cli import …``); PyInstaller runs
# the entry as a top-level script, so the relative imports raise at runtime.
# ``scripts/launch.py`` is a tiny absolute-import wrapper that pulls in the
# package proper.
ENTRY = ROOT / "scripts" / "launch.py"
DATA_FILE = ROOT / "plugins.json"


def _read_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as f:
        return str(tomllib.load(f)["project"]["version"])


def _clean() -> None:
    for path in (DIST, BUILD, SPEC):
        if path.is_dir():
            print(f"  removing {path.relative_to(ROOT)}/")
            shutil.rmtree(path)
        elif path.is_file():
            print(f"  removing {path.relative_to(ROOT)}")
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build cc-plugin-manager.exe")
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Produce a single .exe instead of a folder (slower startup, easier to ship).",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Skip removing build/dist before running PyInstaller.",
    )
    args = parser.parse_args()

    if not ENTRY.is_file():
        print(f"ERROR: entry point not found: {ENTRY}", file=sys.stderr)
        return 2
    if not DATA_FILE.is_file():
        print(f"ERROR: data file not found: {DATA_FILE}", file=sys.stderr)
        return 2

    version = _read_version()
    print(f"[build] cc-plugin-manager v{version}")

    if not args.no_clean:
        print("[build] cleaning previous output")
        _clean()

    # PyInstaller --add-data uses ';' on Windows and ':' on POSIX.
    sep = ";" if sys.platform == "win32" else ":"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        "cc-plugin-manager",
        "--windowed",  # no console window — same effect as pythonw
        "--clean",
        "--noconfirm",
        # Force PyInstaller to bundle every submodule under the package, even
        # ones reached only via dynamic imports (e.g. ui.live_region from a
        # late ``getattr``). Belt-and-braces given how thin our launcher is.
        "--collect-submodules",
        "cc_plugin_manager",
        # Make the project root importable during PyInstaller's analysis
        # phase so ``import cc_plugin_manager`` resolves.
        "--paths",
        str(ROOT),
        "--add-data",
        f"{DATA_FILE}{sep}.",
        str(ENTRY),
    ]
    if args.onefile:
        cmd.insert(cmd.index("--clean"), "--onefile")

    print(f"[build] {' '.join(cmd)}")
    rc = subprocess.run(cmd, cwd=ROOT).returncode
    if rc != 0:
        print(f"[build] PyInstaller exited {rc}", file=sys.stderr)
        return rc

    if args.onefile:
        out = DIST / "cc-plugin-manager.exe"
        print(f"[build] DONE: {out}")
    else:
        out = DIST / "cc-plugin-manager"
        print(f"[build] DONE: {out}\\cc-plugin-manager.exe (onedir)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
