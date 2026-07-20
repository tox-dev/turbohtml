"""markupsafe: the C escape and the Markup operations turbohtml's migration shim succeeds."""

from __future__ import annotations

import functools
from typing import cast

import markupsafe

REQUIREMENTS = ("markupsafe>=3",)

_TEMPLATE = markupsafe.Markup("<li>{}</li><span>{}</span>")
_JOINER = markupsafe.Markup(", ")


def markup(text: str) -> None:
    """Escape a string into a Markup with markupsafe's C-accelerated escape."""
    markupsafe.escape(text)


@functools.cache
def _markup_of(text: str) -> markupsafe.Markup:
    """Return a Markup wrapping the text, cached so the operations time only the method call."""
    return markupsafe.Markup(text)  # ruff:ignore[unsafe-markup-use]  # fixed benchmark fixture, not untrusted input


def markup_op(case: tuple[str, object]) -> None:
    """Run one Markup method (striptags/unescape/format/join) on markupsafe's Markup, by case kind."""
    kind, payload = case
    if kind == "striptags":
        _markup_of(cast("str", payload)).striptags()
    elif kind == "unescape":
        _markup_of(cast("str", payload)).unescape()
    elif kind == "format":
        _TEMPLATE.format(*cast("tuple[str, ...]", payload))
    else:
        _JOINER.join(cast("tuple[str, ...]", payload))


OPERATIONS = {"markup": (markup, "markupsafe"), "markup-op": (markup_op, "markupsafe")}
