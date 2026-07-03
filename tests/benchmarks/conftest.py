"""Put ``tools`` on the path for the ``bench`` registry, and run the benchmarks only under ``--codspeed``.

The benchmarks exist for the CodSpeed CI job, which passes ``--codspeed`` (or sets ``CODSPEED_ENV``); the ordinary test
run skips them so it stays fast, and locally they are opt-in behind the same flag.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1] / "tools"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the benchmarks in this directory unless CodSpeed is driving the run."""
    if config.getoption("--codspeed") or os.environ.get("CODSPEED_ENV") is not None:
        return
    skip = pytest.mark.skip(reason="benchmark: pass --codspeed to run (the CI benchmark job does)")
    for item in items:
        if _HERE in item.path.parents:
            item.add_marker(skip)
