"""linkify-it-py: the pure-Python link scanner markdown-it-py pulls in (finds, does not rewrite)."""

from __future__ import annotations

from linkify_it import LinkifyIt

REQUIREMENTS = ("linkify-it-py>=2.0.3",)

_LINKIFY = LinkifyIt()


def linkify(text: str) -> None:
    """Scan text for links with linkify-it-py's match, which finds the spans but does not rewrite."""
    _LINKIFY.match(text)


def detect(case: tuple[str, str]) -> None:
    """Scan plain text for links with linkify-it-py: match for the spans or test for any link."""
    kind, text = case
    if kind == "find":
        _LINKIFY.match(text)
    else:
        _LINKIFY.test(text)


OPERATIONS = {"linkify": (linkify, "linkify-it-py"), "detect": (detect, "linkify-it-py")}
