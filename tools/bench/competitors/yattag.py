"""
yattag: assemble a tree with the ``tag``/``text`` context managers.

Loaded only inside its own worker venv, where yattag is guaranteed present; the orchestrator reads ``REQUIREMENTS`` and
``OPERATIONS`` statically (via AST) and never imports this module.
"""

from __future__ import annotations

from yattag import Doc

REQUIREMENTS = ("yattag>=1.16.1",)


def build_e(count: int) -> None:
    """Build a ``<ul>`` of rows with yattag's context-manager tags and read the string back."""
    doc, tag, text = Doc().tagtext()
    with tag("ul"):
        for index in range(count):
            with tag("li", ("class", "item"), ("data-i", str(index))):
                text(f"item {index}")
    _ = doc.getvalue()


OPERATIONS = {"build-e": (build_e, "yattag")}
