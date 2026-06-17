"""
Turn URLs and email addresses in HTML into links, the way bleach.linkify did.

bleach is the only library that shipped an HTML-aware linkifier, and it is end of life, so this is its replacement. The
work splits in two: :mod:`turbohtml._html` finds the link spans in a text run in C, and this module does the HTML-aware
part. It parses the input with turbohtml's WHATWG tree builder, walks the text nodes, and leaves alone any text inside
an existing ``<a>`` (so links never nest), inside a raw-text element (``<script>``/``<style>``, whose content is not
prose), or inside a caller's ``skip_tags``. Each found link becomes a :class:`Link` that a chain of ``callbacks`` can
mutate or veto before it is written as an ``<a>``, and the tree serializes back to HTML.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, TypeAlias

from ._html import Element, Text, _linkify_scan, parse_fragment

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_EMAIL_KIND = 1

# A leading ``scheme://`` tells a matched URL already carries its scheme; a bare domain (``example.com``, ``www.x.com``)
# does not and is prefixed with ``http://``. Anchoring at the start avoids treating a ``://`` deeper in a bare domain's
# path (an embedded redirect URL) as the link's own scheme.
_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://")

# Text inside these never becomes a link: an existing anchor (no nested links) and the raw-text elements whose content
# is not markup. A caller's skip_tags is added on top.
_NEVER_LINKIFY = frozenset({"a", "script", "style"})


class Link:
    """
    A link about to be generated, handed to each callback to mutate or veto.

    ``url`` is the ``href``, ``text`` is what the reader sees, and ``attrs`` holds any extra attributes to put on the
    ``<a>`` (``rel``, ``target``, ``class``, ...). A callback returns the link to keep it, or ``None`` to leave the
    matched text unlinked.
    """

    __slots__ = ("attrs", "text", "url")

    def __init__(self, url: str, text: str, attrs: dict[str, str] | None = None) -> None:
        """Create a link from its ``href``, visible ``text``, and optional extra anchor ``attrs``."""
        self.url = url
        self.text = text
        self.attrs = attrs if attrs is not None else {}


# A callback receives the generated :class:`Link` and returns it to keep the link, or ``None`` to leave the text bare.
Callback: TypeAlias = "Callable[[Link], Link | None]"


def nofollow(link: Link) -> Link | None:
    """Add ``rel="nofollow"`` to a web link so search engines skip it; leave ``mailto:`` and other links alone."""
    if link.url.startswith(("http:", "https:")):
        rels = link.attrs.get("rel", "").split()
        if "nofollow" not in rels:
            rels.append("nofollow")
        link.attrs["rel"] = " ".join(rels)
    return link


def target_blank(link: Link) -> Link | None:
    """Open a web link in a new tab; strip a stale ``target`` from a non-web link so it cannot leak through."""
    if link.url.startswith(("http:", "https:")):
        link.attrs["target"] = "_blank"
    else:
        link.attrs.pop("target", None)
    return link


#: The callbacks linkify applies when a caller passes none, matching bleach's default.
DEFAULT_CALLBACKS = (nofollow,)


class Linker:
    """A reusable linkifier; build it once with a configuration and call :meth:`linkify` per document."""

    def __init__(
        self,
        callbacks: Iterable[Callback] = DEFAULT_CALLBACKS,
        skip_tags: Iterable[str] | None = None,
        parse_email: bool = False,  # noqa: FBT001, FBT002  # parse_email is a flag, not a boolean trap, and stays positional
    ) -> None:
        """Configure the callbacks, the tags whose text to leave alone, and whether to autolink email addresses."""
        self.callbacks = list(callbacks)
        self.skip_tags = frozenset(skip_tags or ())
        self.parse_email = parse_email

    def linkify(self, text: str) -> str:
        """Linkify HTML and return it, leaving everything but eligible text runs untouched."""
        root = parse_fragment(text)
        self._walk(root, linkifiable=True)
        return root.inner_html

    def _walk(self, element: Element, *, linkifiable: bool) -> None:
        """Recurse, linkifying text only where it is not inside an anchor, raw-text element, or skip tag."""
        for child in list(element.children):
            if isinstance(child, Text):
                if linkifiable:
                    self._linkify_text(child)
            elif isinstance(child, Element):
                nested = linkifiable and child.tag not in _NEVER_LINKIFY and child.tag not in self.skip_tags
                self._walk(child, linkifiable=nested)

    def _linkify_text(self, node: Text) -> None:
        """Replace a text node with the text and anchors that the link spans in it imply."""
        data = node.data
        spans = _linkify_scan(data, self.parse_email, True)  # noqa: FBT003  # C call is positional; True enables bare domains
        if not spans:
            return
        pieces: list[Element | Text] = []
        pos = 0
        for start, end, kind in spans:
            if start > pos:
                pieces.append(Text(data[pos:start]))
            anchor = self._build_anchor(data[start:end], kind)
            pieces.append(anchor if anchor is not None else Text(data[start:end]))
            pos = end
        if pos < len(data):
            pieces.append(Text(data[pos:]))
        node.replace_with(*pieces)

    def _build_anchor(self, matched: str, kind: int) -> Element | None:
        """Build the ``<a>`` for one matched link, running the callbacks; return ``None`` if a callback vetoes it."""
        if kind == _EMAIL_KIND:
            url = "mailto:" + matched
        elif _SCHEME.match(matched):
            url = matched
        else:
            url = "http://" + matched
        link = Link(url, matched)
        for callback in self.callbacks:
            result = callback(link)
            if result is None:
                return None
            link = result
        anchor = Element("a", {"href": link.url, **link.attrs})
        anchor.append(Text(link.text))
        return anchor


def linkify(
    text: str,
    callbacks: Iterable[Callback] = DEFAULT_CALLBACKS,
    skip_tags: Iterable[str] | None = None,
    parse_email: bool = False,  # noqa: FBT001, FBT002  # parse_email is a flag, not a boolean trap, and stays positional
) -> str:
    """Find URLs and email addresses in HTML and wrap them in ``<a>`` links, leaving existing markup untouched."""
    return Linker(callbacks, skip_tags, parse_email).linkify(text)


__all__ = [
    "DEFAULT_CALLBACKS",
    "Callback",
    "Link",
    "Linker",
    "linkify",
    "nofollow",
    "target_blank",
]
