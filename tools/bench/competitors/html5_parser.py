"""html5-parser: the gumbo C parser returning an lxml tree."""

from __future__ import annotations

from typing import Final

import html5_parser

REQUIREMENTS: Final[tuple[str, ...]] = ("html5-parser>=0.4.12", "lxml>=6.1.1")
# html5-parser's wheel bundles libxml2 and returns an lxml tree, so lxml must link the identical libxml2 or the import
# aborts. Building lxml from source binds it to the system libxml2 the html5-parser wheel was built against, matching.
_PIP_OPTIONS: Final[tuple[str, ...]] = ("--no-binary", "lxml")


def parse(text: str) -> None:
    # Parse a whole document with html5-parser (gumbo, into an lxml tree).
    html5_parser.parse(text)


OPERATIONS = {
    "parse": (parse, "html5-parser"),
    "parse-formatting": (parse, "html5-parser"),
    "parse-foster": (parse, "html5-parser"),
    "parse-crlf": (parse, "html5-parser"),
    "parse-nul": (parse, "html5-parser"),
    "parse-afe": (parse, "html5-parser"),
    "parse-scope": (parse, "html5-parser"),
}
