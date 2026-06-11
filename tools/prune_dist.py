#!/usr/bin/env python3
"""
Drop the vendored git submodules from the sdist staging tree.

Meson dist archives initialized submodules, so without this the sdist content
would depend on which submodules happen to be checked out locally; the corpora
(conformance data and benchmark documents) are development-only either way.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

if __name__ == "__main__":
    root = Path(os.environ["MESON_DIST_ROOT"])
    for relative in ("tests/html5lib-tests", "tools/bench-data", "tools/html5lib-python"):
        shutil.rmtree(root / relative, ignore_errors=True)
