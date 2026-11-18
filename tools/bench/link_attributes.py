"""
The URLs turbohtml's links() reads, so every competitor's links-extract walks the same attributes.

Mirrors the attribute table in src/turbohtml/_c/extract/links.c. Not mirrored: srcset and imagesrcset candidates, which
turbohtml filters by a descriptor grammar of its own, the <meta http-equiv=refresh> target, and @import strings. No
links-extract case carries any of them, so the outputs agree on every timed input.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_SINGLE_URL: Final[frozenset[str]] = frozenset((
    "action",
    "background",
    "cite",
    "data",
    "formaction",
    "href",
    "longdesc",
    "poster",
    "src",
    "xlink:href",
))
_SPACE_LIST_ELEMENTS: Final[MappingProxyType[str, frozenset[str]]] = MappingProxyType({
    "archive": frozenset(("applet", "object")),
    "ping": frozenset(("a", "area")),
})
_SPACE_TOKEN: Final[re.Pattern[str]] = re.compile(r"[^ \t\n\r\f]+")
_CSS_URL: Final[re.Pattern[str]] = re.compile(
    r"""
    (?<![\w-])url\(
    \s*
    (?:
        '([^']*)'    # single-quoted string
        | "([^"]*)"  # double-quoted string
        | ([^'"\s)]*)  # unquoted url token
    )
    \s*\)
    """,
    re.IGNORECASE | re.VERBOSE,
)
LINK_ELEMENTS_SELECTOR: Final[str] = ", ".join((
    *("[{}]".format(name.replace(":", "\\:")) for name in (*sorted(_SINGLE_URL), *_SPACE_LIST_ELEMENTS, "style")),
    "style",
))


def element_links(
    tag: str | None, attributes: Iterable[tuple[str, str | Sequence[str] | None]], style: str | None
) -> list[str]:
    # BeautifulSoup splits multi-valued attributes such as archive into lists; rejoin them to the raw value.
    links: list[str] = []
    for name, value in attributes:
        if value is None:
            continue
        raw = value if isinstance(value, str) else " ".join(value)
        if name in _SINGLE_URL:
            if url := raw.strip(" \t\n\r\f"):
                links.append(url)
        elif name == "style":
            links.extend(_css_urls(raw))
        elif name in _SPACE_LIST_ELEMENTS and tag in _SPACE_LIST_ELEMENTS[name]:
            links.extend(_SPACE_TOKEN.findall(raw))
    if style is not None:
        links.extend(_css_urls(style))
    return links


def _css_urls(css: str) -> list[str]:
    return [url for quoted, double, bare in _CSS_URL.findall(css) if (url := quoted or double or bare)]


__all__ = [
    "LINK_ELEMENTS_SELECTOR",
    "element_links",
]
