"""PyInstaller entry point — re-exports the package's main as a top-level script.

PyInstaller runs the script it's pointed at directly (no ``-m`` package
context), so a script that uses relative imports like ``from .cli import …``
will fail at runtime with ``ImportError: attempted relative import with no
known parent package``.

The fix is to point PyInstaller at THIS file instead of
``cc_plugin_manager/__main__.py``. From here, ``cc_plugin_manager`` is just a
regular absolute import; PyInstaller's analysis follows the package, the
relative imports inside the package resolve normally, and the bundled .exe
behaves identically to ``python -m cc_plugin_manager``.
"""

from __future__ import annotations

import sys

from cc_plugin_manager.__main__ import main


if __name__ == "__main__":
    sys.exit(main())
