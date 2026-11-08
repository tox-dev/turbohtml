"""jsmin: the Python port of Crockford's jsmin, a character-level state machine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from jsmin import jsmin

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("jsmin>=3.0",)


def minify_js(source: str) -> str:
    # Minify JavaScript with jsmin's Crockford-style character state machine.
    return jsmin(source)


OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "minify-js-guards": (minify_js, "jsmin"),
    "minify-js-propagation": (minify_js, "jsmin"),
    "minify-js-var-initialization": (minify_js, "jsmin"),
    "minify-js-unused-declarations": (minify_js, "jsmin"),
    "minify-js-unlink": (minify_js, "jsmin"),
    "minify-js-single-use": (minify_js, "jsmin"),
    "minify-js-sequences": (minify_js, "jsmin"),
    "minify-js": (minify_js, "jsmin"),
    "minify-js-names": (minify_js, "jsmin"),
}
