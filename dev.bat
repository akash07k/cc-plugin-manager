@echo off
setlocal EnableDelayedExpansion

REM ===========================================================================
REM dev.bat -- developer command runner for cc-plugin-manager.
REM
REM Two modes:
REM
REM   Interactive menu (no args):
REM     dev.bat
REM
REM   Direct dispatch (for scripting / CI):
REM     dev.bat install
REM     dev.bat test -k load_config
REM     dev.bat check
REM     dev.bat build
REM
REM Subcommand reference (also reachable via the menu):
REM   help, version
REM   install / sync
REM   upgrade
REM   test [pytest-args...]
REM   cov / coverage
REM   lint
REM   format / fmt
REM   format-check
REM   types / typecheck
REM   check / all / ci
REM   build / exe [--onefile]
REM   clean
REM   run [forwarded args...]
REM ===========================================================================

cd /d "%~dp0"

set "CMD=%~1"
if "%CMD%"=="" goto :menu_loop

REM Capture every arg AFTER the first into REST. The `tokens=1,* delims= `
REM trick splits on the first space and assigns the tail to %%b.
set "REST="
for /f "tokens=1,* delims= " %%a in ("%*") do set "REST=%%b"

REM ---------------------------------------------------------------------------
REM Direct dispatch
REM ---------------------------------------------------------------------------
if /i "%CMD%"=="help"          goto :cmd_help
if /i "%CMD%"=="-h"            goto :cmd_help
if /i "%CMD%"=="--help"        goto :cmd_help
if /i "%CMD%"=="clean"         goto :cmd_clean

REM Everything below this point needs uv.
where uv >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: 'uv' is not installed or not on PATH.
    echo.
    echo   Install uv from https://docs.astral.sh/uv/getting-started/installation/
    echo   On Windows the easiest option is:
    echo       powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo.
    exit /b 127
)

if /i "%CMD%"=="install"       goto :cmd_install
if /i "%CMD%"=="sync"          goto :cmd_install
if /i "%CMD%"=="upgrade"       goto :cmd_upgrade
if /i "%CMD%"=="test"          goto :cmd_test
if /i "%CMD%"=="cov"           goto :cmd_cov
if /i "%CMD%"=="coverage"      goto :cmd_cov
if /i "%CMD%"=="lint"          goto :cmd_lint
if /i "%CMD%"=="format"        goto :cmd_format
if /i "%CMD%"=="fmt"           goto :cmd_format
if /i "%CMD%"=="format-check"  goto :cmd_formatcheck
if /i "%CMD%"=="typecheck"     goto :cmd_types
if /i "%CMD%"=="types"         goto :cmd_types
if /i "%CMD%"=="check"         goto :cmd_check
if /i "%CMD%"=="all"           goto :cmd_check
if /i "%CMD%"=="ci"            goto :cmd_check
if /i "%CMD%"=="build"         goto :cmd_build
if /i "%CMD%"=="exe"           goto :cmd_build
if /i "%CMD%"=="run"           goto :cmd_run
if /i "%CMD%"=="version"       goto :cmd_version

echo.
echo   Unknown command: %CMD%
echo   Run 'dev.bat help' for the list of commands.
echo.
exit /b 2


REM ===========================================================================
REM Interactive menu
REM ===========================================================================

:menu_loop
echo.
echo ===========================================================================
echo   cc-plugin-manager -- developer command runner
echo ===========================================================================
echo.
echo   Setup
echo     [1]  Install / sync dependencies                  (uv sync --extra dev)
echo     [2]  Upgrade all dependencies                     (uv lock --upgrade)
echo     [3]  Show versions (Python, uv)
echo.
echo   Tests
echo     [4]  Run all tests                                (pytest)
echo     [5]  Run tests + coverage report                  (HTML + term)
echo.
echo   Code quality
echo     [6]  Lint                                         (ruff check)
echo     [7]  Format code (modifies files)                 (ruff format)
echo     [8]  Format check (no modifications)              (ruff format --check)
echo     [9]  Type check (mypy strict on data/cli/worker)
echo     [10] Full CI gate                                 (lint + format-check + types + tests)
echo.
echo   Build / run / clean
echo     [11] Build standalone cc-plugin-manager.exe       (PyInstaller, onedir)
echo     [12] Build single-file .exe                       (PyInstaller, onefile)
echo     [13] Launch cc-plugin-manager GUI
echo     [14] Clean caches and build artefacts
echo.
echo     [h]  Show subcommand reference (text help)
echo     [q]  Quit
echo.

set "CHOICE="
set /p CHOICE="  Your choice: "

if "!CHOICE!"==""              goto :menu_loop
if /i "!CHOICE!"=="q"          goto :menu_end
if /i "!CHOICE!"=="quit"       goto :menu_end
if /i "!CHOICE!"=="exit"       goto :menu_end
if /i "!CHOICE!"=="h"          goto :menu_show_help
if /i "!CHOICE!"=="help"       goto :menu_show_help
if /i "!CHOICE!"=="?"          goto :menu_show_help

REM ---- Validate the choice is a known menu number ----
set "VALID=0"
for %%c in (1 2 3 4 5 6 7 8 9 10 11 12 13 14) do (
    if "!CHOICE!"=="%%c" set "VALID=1"
)
if "!VALID!"=="0" (
    echo.
    echo   Unknown choice: !CHOICE!
    echo   Type a number from 1-14, or 'h' for help, 'q' to quit.
    echo.
    goto :menu_loop
)

REM ---- Some commands need uv; check before dispatching ----
where uv >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: 'uv' is not installed or not on PATH.
    echo   Install from https://docs.astral.sh/uv/ then retry.
    echo.
    pause
    goto :menu_loop
)

REM ---- Dispatch ----
if "!CHOICE!"=="1"  call :cmd_install
if "!CHOICE!"=="2"  call :cmd_upgrade
if "!CHOICE!"=="3"  call :cmd_version
if "!CHOICE!"=="4"  call :cmd_test
if "!CHOICE!"=="5"  call :cmd_cov
if "!CHOICE!"=="6"  call :cmd_lint
if "!CHOICE!"=="7"  call :cmd_format
if "!CHOICE!"=="8"  call :cmd_formatcheck
if "!CHOICE!"=="9"  call :cmd_types
if "!CHOICE!"=="10" call :cmd_check
if "!CHOICE!"=="11" call :cmd_build
if "!CHOICE!"=="12" call :cmd_build_onefile
if "!CHOICE!"=="13" call :cmd_run
if "!CHOICE!"=="14" call :cmd_clean

set "LAST=!ERRORLEVEL!"
echo.
echo   ----- command finished (exit=!LAST!) -----
echo.
pause
goto :menu_loop

:menu_show_help
call :cmd_help
echo.
pause
goto :menu_loop

:menu_end
echo.
echo   Bye.
exit /b 0


REM ===========================================================================
REM Subcommand labels (callable via direct dispatch OR menu)
REM ===========================================================================

:cmd_help
echo.
echo   cc-plugin-manager -- developer command runner
echo.
echo   USAGE
echo     dev.bat                       Show interactive menu
echo     dev.bat ^<command^> [args...]   Direct dispatch (one-shot)
echo.
echo   COMMANDS
echo     help                    Show this help message
echo     version                 Show Python and uv versions
echo.
echo     install   sync          Sync dependencies (dev + build extras)
echo     upgrade                 Upgrade all locked deps to latest compatible
echo     test                    Run pytest (any args forwarded)
echo     cov       coverage      Run pytest with coverage (HTML + term)
echo.
echo     lint                    ruff check
echo     format    fmt           ruff format (modifies files)
echo     format-check            ruff format --check (no modifications)
echo     types     typecheck     mypy strict on data/cli/worker
echo     check     all  ci       Full gate: lint + format-check + types + tests
echo.
echo     build     exe           Build standalone cc-plugin-manager.exe
echo                             (add --onefile for a single .exe)
echo     clean                   Remove caches, build/, dist/, coverage output
echo     run                     Launch the GUI (uv run python -m cc_plugin_manager)
echo.
echo   EXAMPLES
echo     dev.bat                                   (open menu)
echo     dev.bat install
echo     dev.bat test -v -k load_config
echo     dev.bat check
echo     dev.bat build
echo     dev.bat build --onefile
echo     dev.bat run
echo.
exit /b 0


:cmd_install
echo [install] uv sync --extra dev --extra build
uv sync --extra dev --extra build
exit /b !ERRORLEVEL!


:cmd_upgrade
echo [upgrade] uv lock --upgrade
uv lock --upgrade
if errorlevel 1 exit /b !ERRORLEVEL!
echo.
echo [upgrade] uv sync --extra dev --extra build
uv sync --extra dev --extra build
exit /b !ERRORLEVEL!


:cmd_test
echo [test] uv run pytest -q !REST!
uv run pytest -q !REST!
exit /b !ERRORLEVEL!


:cmd_cov
echo [cov] uv run pytest --cov=cc_plugin_manager --cov-report=term-missing --cov-report=html:.coverage_html !REST!
uv run pytest --cov=cc_plugin_manager --cov-report=term-missing --cov-report=html:.coverage_html !REST!
set "COV_EXIT=!ERRORLEVEL!"
echo.
echo   HTML coverage report: .coverage_html\index.html
exit /b !COV_EXIT!


:cmd_lint
echo [lint] uv run ruff check .
uv run ruff check . !REST!
exit /b !ERRORLEVEL!


:cmd_format
echo [format] uv run ruff format .
uv run ruff format .
exit /b !ERRORLEVEL!


:cmd_formatcheck
echo [format-check] uv run ruff format --check .
uv run ruff format --check .
exit /b !ERRORLEVEL!


:cmd_types
echo [types] uv run mypy cc_plugin_manager/data.py cc_plugin_manager/cli.py cc_plugin_manager/worker.py
uv run mypy cc_plugin_manager/data.py cc_plugin_manager/cli.py cc_plugin_manager/worker.py
exit /b !ERRORLEVEL!


:cmd_check
echo [check] running all CI gates: lint, format-check, types, tests
echo.

call :cmd_lint
if errorlevel 1 (
    echo.
    echo [check] FAIL at lint
    exit /b 1
)
echo.

call :cmd_formatcheck
if errorlevel 1 (
    echo.
    echo [check] FAIL at format-check
    echo [check] hint: 'dev.bat format' will auto-fix.
    exit /b 1
)
echo.

call :cmd_types
if errorlevel 1 (
    echo.
    echo [check] FAIL at types
    exit /b 1
)
echo.

call :cmd_test
if errorlevel 1 (
    echo.
    echo [check] FAIL at tests
    exit /b 1
)
echo.

echo [check] ALL CHECKS PASSED
exit /b 0


:cmd_build
echo [build] ensuring build extras are installed...
uv sync --extra dev --extra build
if errorlevel 1 exit /b !ERRORLEVEL!
echo.
echo [build] uv run python scripts/build_exe.py !REST!
uv run python scripts/build_exe.py !REST!
exit /b !ERRORLEVEL!


:cmd_build_onefile
REM Menu shortcut: option 12 (single-file .exe).
echo [build] ensuring build extras are installed...
uv sync --extra dev --extra build
if errorlevel 1 exit /b !ERRORLEVEL!
echo.
echo [build] uv run python scripts/build_exe.py --onefile
uv run python scripts/build_exe.py --onefile
exit /b !ERRORLEVEL!


:cmd_run
echo [run] uv run python -m cc_plugin_manager !REST!
uv run python -m cc_plugin_manager !REST!
exit /b !ERRORLEVEL!


:cmd_clean
echo [clean] removing caches and build artefacts...
for %%d in (build dist .coverage_html htmlcov .pytest_cache .mypy_cache .ruff_cache .hypothesis) do (
    if exist "%%d\" (
        echo   removing %%d
        rmdir /s /q "%%d" 2>nul
    )
)
if exist .coverage (
    echo   removing .coverage
    del /q .coverage 2>nul
)
if exist cc-plugin-manager.spec (
    echo   removing cc-plugin-manager.spec
    del /q cc-plugin-manager.spec 2>nul
)
for /d /r "cc_plugin_manager" %%d in (__pycache__) do if exist "%%d\" rmdir /s /q "%%d" 2>nul
for /d /r "tests" %%d in (__pycache__) do if exist "%%d\" rmdir /s /q "%%d" 2>nul
echo [clean] done.
exit /b 0


:cmd_version
echo [version]
uv run python --version
uv --version
exit /b 0
