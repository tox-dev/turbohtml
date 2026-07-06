"""calmjs.parse: a full ES5 parser in Python with an obfuscating minifying printer."""

from __future__ import annotations

from calmjs.parse import es5
from calmjs.parse.unparsers.es5 import minify_print

REQUIREMENTS = ("calmjs.parse>=1.3",)


def minify_js(source: str) -> str:
    """Minify JavaScript by parsing to an ES5 AST and printing it obfuscated."""
    return minify_print(es5(source), obfuscate=True)


OPERATIONS = {"minify-js": (minify_js, "calmjs.parse")}
