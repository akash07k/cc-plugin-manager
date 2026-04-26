@echo off
setlocal EnableDelayedExpansion

REM ===========================================================================
REM run.bat -- robust launcher for cc-plugin-manager.
REM
REM Verifies uv is installed, ensures the project's virtual environment is
REM up-to-date with pyproject.toml on the first run, then launches the GUI
REM via pythonw (no console window).
REM
REM Double-click friendly: when invoked with no args from Explorer, errors
REM during setup keep the window open so the user can read the message.
REM ===========================================================================

REM Anchor working dir to the script location so plugins.json and any other
REM relative paths resolve predictably even when invoked via shortcut.
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM 1. Verify uv is installed and on PATH.
REM ---------------------------------------------------------------------------
where uv >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: 'uv' is not installed or not on PATH.
    echo.
    echo   Install uv from https://docs.astral.sh/uv/getting-started/installation/
    echo   On Windows the easiest option is:
    echo       powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo.
    pause
    exit /b 127
)

REM ---------------------------------------------------------------------------
REM 2. First-run setup: build the virtual environment and install dependencies.
REM    "uv sync" is idempotent and fast on subsequent runs (no-op when
REM    up-to-date), so we only show the "first run" banner if the venv does
REM    not exist yet.
REM ---------------------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   First run detected -- setting up the Python environment with uv...
    echo   This installs Python 3.11+ if missing, then resolves dependencies.
    echo.
    uv sync --extra dev
    if errorlevel 1 (
        echo.
        echo   ERROR: 'uv sync' failed. See messages above.
        echo.
        pause
        exit /b 1
    )
    echo.
    echo   Setup complete. Launching the GUI...
    echo.
)

REM ---------------------------------------------------------------------------
REM 3. Launch the GUI without a console. We resolve pythonw.exe under the
REM    project's .venv so users don't need to activate the venv themselves.
REM    Falls back to a system pythonw if for some reason the venv copy is
REM    missing.
REM ---------------------------------------------------------------------------
set "PYW=.venv\Scripts\pythonw.exe"
if not exist "!PYW!" (
    where pythonw >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   ERROR: pythonw not found in .venv\Scripts and no system pythonw on PATH.
        echo.
        pause
        exit /b 1
    )
    set "PYW=pythonw"
)

REM Forward any arguments through to the module entry point (none today, but
REM future-proof for --config / --debug flags).
start "" "!PYW!" -m cc_plugin_manager %*

exit /b 0
