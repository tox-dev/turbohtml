from __future__ import annotations

import re
from pathlib import Path

_PYTHON_BLOCKS = re.findall(
    r"```python\n(.*?)```",
    (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8"),
    re.DOTALL,
)


def test_readme_examples_run() -> None:
    namespace: dict[str, object] = {}
    for block in _PYTHON_BLOCKS:
        exec(block, namespace)  # ruff:ignore[exec-builtin]  # our own README, run as one continuous script
