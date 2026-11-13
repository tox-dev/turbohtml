from __future__ import annotations

import re
from pathlib import Path
from types import ModuleType
from typing import Final

_PYTHON_BLOCKS: Final[list[str]] = re.findall(
    r"```python\n(.*?)```",
    (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8"),
    re.DOTALL,
)


def test_readme_examples_run() -> None:
    assert _PYTHON_BLOCKS, "README.md lost its python examples"
    namespace = ModuleType("readme").__dict__
    for block in _PYTHON_BLOCKS:
        exec(block, namespace)  # ruff:ignore[exec-builtin]  # our own README, run as one continuous script
