"""
One side of the previous-release differential (``release_diff.py``): run operations on inputs and report results.

It reads one JSON ``[operation, input]`` pair per stdin line and answers one JSON line: ``{"ok": result}``,
``{"error": [type, message]}``, or ``{"missing": reason}`` when this turbohtml lacks the operation. It imports turbohtml
lazily and only through public names that the latest release also ships, so the same file runs under HEAD and under an
installed wheel.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Document


def main() -> int:
    """Answer requests until stdin closes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--perturb", default=None, help="append a marker to this operation's result (negative control)")
    args = parser.parse_args()
    for line in sys.stdin:
        operation, text = json.loads(line)
        print(json.dumps(_answer(operation, text, perturb=operation == args.perturb)), flush=True)
    return 0


def _answer(operation: str, text: str, *, perturb: bool) -> dict[str, object]:
    try:
        function = _OPERATIONS[operation]()
    except (ImportError, AttributeError) as error:
        return {"missing": f"{type(error).__name__}: {error}"}
    try:
        result = function(text)
    except Exception as error:  # ruff: ignore[blind-except]  # any exception is a result to compare across versions
        return {"error": [type(error).__name__, str(error)]}
    return {"ok": f"{result}\0perturbed" if perturb else result}


def _html(render: Callable[[Document], object]) -> Callable[[], Callable[[str], object]]:
    def build() -> Callable[[str], object]:
        from turbohtml import parse  # ruff: ignore[import-outside-top-level]  # imported per worker so a missing name reports per operation

        return lambda text: render(parse(text))

    return build


def _xml() -> Callable[[str], object]:
    from turbohtml import Html, parse  # ruff: ignore[import-outside-top-level]  # see _html

    return lambda text: parse(text).serialize(Html(xml=True))


def _fragment() -> Callable[[str], object]:
    from turbohtml import parse_fragment  # ruff: ignore[import-outside-top-level]  # see _html

    return lambda text: parse_fragment(text).inner_html


def _clean(name: str, *arguments: object) -> Callable[[], Callable[[str], object]]:
    def build() -> Callable[[str], object]:
        from turbohtml import clean  # ruff: ignore[import-outside-top-level]  # see _html

        function = getattr(clean, name)
        return lambda text: function(text, *arguments)

    return build


def _plain_js() -> Callable[[str], object]:
    from turbohtml.clean import JSMinify, minify_js  # ruff: ignore[import-outside-top-level]  # see _html

    return lambda text: minify_js(text, JSMinify(mangle=False, fold=False))


def _style() -> Callable[[str], object]:
    from turbohtml.cssom import StyleDeclaration  # ruff: ignore[import-outside-top-level]  # see _html

    return lambda text: StyleDeclaration.parse(text).text


def _select() -> Callable[[str], object]:
    from turbohtml import parse  # ruff: ignore[import-outside-top-level]  # see _html

    def run(case: str) -> object:
        payload = json.loads(case)
        return [element.html for element in parse(payload["html"]).select(payload["css"])]

    return run


def _css_to_xpath() -> Callable[[str], object]:
    from turbohtml.convert import css_to_xpath  # ruff: ignore[import-outside-top-level]  # see _html

    return lambda case: css_to_xpath(json.loads(case)["css"])


def _xpath() -> Callable[[str], object]:
    from turbohtml import Node, parse  # ruff: ignore[import-outside-top-level]  # see _html

    def run(case: str) -> object:
        payload = json.loads(case)
        result = parse(payload["html"]).xpath(payload["xpath"])
        items = result if isinstance(result, list) else [result]
        return [item.html if isinstance(item, Node) else repr(item) for item in items]

    return run


_OPERATIONS: Final[dict[str, Callable[[], Callable[[str], object]]]] = {
    "serialize": _html(lambda document: document.serialize()),
    "fragment": _fragment,
    "xml": _xml,
    "text": _html(lambda document: document.to_text()),
    "markdown": _html(lambda document: document.to_markdown()),
    "minify_html": _clean("minify"),
    "sanitize": _clean("sanitize"),
    "minify_css": _clean("minify_css"),
    "minify_js": _clean("minify_js"),
    "minify_js_plain": _plain_js,
    "style": _style,
    "select": _select,
    "css_to_xpath": _css_to_xpath,
    "xpath": _xpath,
}


if __name__ == "__main__":
    raise SystemExit(main())
