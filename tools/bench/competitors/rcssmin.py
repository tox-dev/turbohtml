"""rcssmin: the non-destructive C/Python whitespace-and-comment stripper."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import rcssmin

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("rcssmin>=1.2",)


def minify_css(css: str) -> str:
    # Minify a stylesheet with rcssmin's non-destructive cssmin.
    return rcssmin.cssmin(css)


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "minify-css": (minify_css, "rcssmin"),
    "minify-css-merges": (minify_css, "rcssmin"),
    "minify-css-conflicts": (minify_css, "rcssmin"),
}
