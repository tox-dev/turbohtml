"""linkify-it-py: the pure-Python link scanner markdown-it-py pulls in (finds, does not rewrite)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from linkify_it import LinkifyIt

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("linkify-it-py>=2.0.3",)

_LINKIFY: Final[LinkifyIt] = LinkifyIt()


def detect(case: tuple[str, str]) -> None:
    # Scan plain text for links with linkify-it-py: match for the spans or test for any link.
    kind, text = case
    if kind == "find":
        _LINKIFY.match(text)
    else:
        _LINKIFY.test(text)


# linkify-it-py only finds link spans in plain text; it never rewrites HTML, so it maps to detect, not the parse-and-
# rewrite linkify op -- comparing its plain-text scan against turbohtml's full linkify would not be like-for-like.
OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {"detect": (detect, "linkify-it-py")}
