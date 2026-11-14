from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

import pytest

from turbohtml import _html  # ruff:ignore[import-private-name]  # work-step build hook

_HERE: Final[Path] = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1] / "tools"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Skip the benchmarks in this directory unless CodSpeed is driving the run.
    if config.getoption("--codspeed") or os.environ.get("CODSPEED_ENV") is not None:
        if hasattr(_html, "_work_steps"):
            msg = "CodSpeed timings must come from a build without -Dwork_steps=true"
            raise pytest.UsageError(msg)
        return
    skip = pytest.mark.skip(reason="benchmark: pass --codspeed to run (the CI benchmark job does)")
    for item in items:
        if _HERE in item.path.parents:
            item.add_marker(skip)
