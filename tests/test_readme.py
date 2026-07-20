"""The README's Python examples must run, so the quickstart never goes stale.

The reStructuredText docs validate their output through Sphinx's doctest builder
(``tox -e docs``); the README is Markdown, so its ``python`` blocks are executed
here in one shared namespace, top to bottom, exactly as a reader following along
would run them.
"""

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
