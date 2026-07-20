"""
Turn URLs and email addresses in HTML into links, the way bleach.linkify did.

bleach is the only library that shipped an HTML-aware linkifier, and it is end of life, so this is its replacement. The
work splits in two: :mod:`turbohtml._html` finds the link spans in a text run in C, and this module does the HTML-aware
part. It parses the input with turbohtml's WHATWG tree builder, walks the text nodes, and leaves alone any text inside
an existing ``<a>`` (so links never nest), inside a raw-text element (``<script>``/``<style>``, whose content is not
prose), or inside a caller's ``skip_tags``. Each found link becomes a :class:`LinkCandidate` that a chain of
``callbacks`` can mutate or veto before it is written as an ``<a>``, and the tree serializes back to HTML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeAlias

from turbohtml._html import Element, Text, _linkify_find, _linkify_scan, parse_fragment

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_EMAIL_KIND: Final = 1

# A scheme-less match (``tel:+1-800``, ``bitcoin:1abc``) already carries its scheme, so its url is the matched text
# verbatim; only a bare domain (kind 0 without a ``scheme://``) is prefixed with ``http://``.
_SCHEME_KIND: Final = 2

# A leading ``scheme://`` tells a matched URL already carries its scheme; a bare domain (``example.com``, ``www.x.com``)
# does not and is prefixed with ``http://``. Anchoring at the start avoids treating a ``://`` deeper in a bare domain's
# path (an embedded redirect URL) as the link's own scheme.
_SCHEME: Final = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://")

# The ``scheme://host`` schemes autolinked when a config registers none: the fixed set linkify-it recognizes, so a typo
# scheme or a ``javascript://`` payload stays plain text. A ``Linkify.schemes`` restricts to its own set (bleach), while
# a ``LinkDetector``'s ``schemes`` extends this one; the low-level scanner without an allowlist stays permissive.
_DEFAULT_URL_SCHEMES: Final = ("ftp", "http", "https")

# Text inside these never becomes a link: an existing anchor (no nested links) and the raw-text elements whose content
# is not markup. A caller's skip_tags is added on top.
_NEVER_LINKIFY: Final = frozenset({"a", "script", "style"})


class LinkCandidate:
    """
    A link handed to each callback to mutate or veto.

    A callback returns the link to keep it, or ``None`` to drop the anchor: a detected link stays plain text, an
    existing one is unwrapped to its contents.

    :param url: the link's ``href``.
    :param text: the visible link text the reader sees.
    :param attrs: extra attributes to put on the ``<a>`` (``rel``, ``target``, ``class``, ...).
    :param existing: True when reprocessing an ``<a>`` already in the input, False for a freshly detected link.
    """

    __slots__ = ("attrs", "existing", "text", "url")

    def __init__(
        self,
        url: str,
        text: str,
        attrs: dict[str, str] | None = None,
        *,
        existing: bool = False,
    ) -> None:
        """Create a link."""
        self.url = url
        self.text = text
        self.attrs = attrs if attrs is not None else {}
        self.existing = existing


# A callback receives the generated :class:`LinkCandidate` and returns it to keep the link, or ``None`` to leave the
# text bare.
Callback: TypeAlias = "Callable[[LinkCandidate], LinkCandidate | None]"


def _attr_str(value: str | list[str] | None) -> str:
    """Flatten an attribute value to a string for a callback: a token list joins, a bare name is empty."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(value)
    return value


def _is_web_url(url: str) -> bool:
    """Is this an ``http``/``https`` URL? The scheme is matched case-insensitively, so ``HTTP://`` counts."""
    return url[:6].lower().startswith(("http:", "https:"))


def nofollow(link: LinkCandidate) -> LinkCandidate | None:
    """
    Add ``rel="nofollow"`` to a web link so search engines skip it, leaving ``mailto:`` and other links alone.

    :param link: the link to adjust.
    :returns: the link, with ``nofollow`` added when it is a web link.
    """
    if _is_web_url(link.url):
        rels = link.attrs.get("rel", "").split()
        if "nofollow" not in rels:
            rels.append("nofollow")
        link.attrs["rel"] = " ".join(rels)
    return link


def target_blank(link: LinkCandidate) -> LinkCandidate | None:
    """
    Open a web link in a new tab, stripping a stale ``target`` from a non-web link so it cannot leak through.

    :param link: the link to adjust.
    :returns: the link, with ``target`` set on a web link or cleared on a non-web link.
    """
    if _is_web_url(link.url):
        link.attrs["target"] = "_blank"
    else:
        link.attrs.pop("target", None)
    return link


#: The callbacks linkify applies when a caller passes none, matching bleach's default.
DEFAULT_CALLBACKS: Final = (nofollow,)


@dataclass(frozen=True)
class Linkify:
    """
    An immutable, thread-safe description of how :func:`linkify` and :class:`Linker` find and rewrite links.

    Build one and reuse it across threads.

    :param callbacks: callables run on each detected link to adjust or veto it (defaults to ``DEFAULT_CALLBACKS``).
    :param skip_tags: tags whose text is left untouched, such as ``pre`` and ``code``.
    :param parse_email: also autolink bare email addresses as ``mailto:`` links.
    :param process_existing: run the callbacks over ``<a>`` tags already present, not only freshly detected links.
    :param extra_tlds: top-level domains that make a bare domain a link, on top of the built-in IANA table.
    :param schemes: the exact set of ``scheme://`` URL schemes that autolink; ``None`` keeps the built-in
        ``http``/``https``/``ftp`` default, so a typo scheme or a ``javascript://`` payload stays plain text. A bare
        domain is always treated as ``http`` and is governed by the TLD table, not ``schemes``.
    """

    callbacks: Iterable[Callback] = DEFAULT_CALLBACKS
    skip_tags: Iterable[str] | None = None
    parse_email: bool = False
    process_existing: bool = False
    extra_tlds: Iterable[str] | None = None
    schemes: Iterable[str] | None = None


class Linker:
    """
    A reusable linkifier; build it once from a :class:`Linkify` configuration and call :meth:`linkify` per document.

    :param options: the configuration to apply; None uses ``DEFAULT_CALLBACKS`` and detects nothing else.
    """

    def __init__(self, options: Linkify | None = None) -> None:
        """Compile a configuration into the form the walk consumes."""
        config = options if options is not None else Linkify()
        self.callbacks = list(config.callbacks)
        self.skip_tags = (
            frozenset(tag.lower() for tag in config.skip_tags) if config.skip_tags is not None else frozenset()
        )
        self.parse_email = config.parse_email
        self.process_existing = config.process_existing
        self.extra_tlds = tuple(sorted({tld.lower() for tld in config.extra_tlds})) if config.extra_tlds else ()
        self.url_schemes = (
            tuple(sorted({scheme.lower() for scheme in config.schemes}))
            if config.schemes is not None
            else _DEFAULT_URL_SCHEMES
        )

    def linkify(self, text: str) -> str:
        """
        Linkify HTML, leaving everything but eligible text runs untouched.

        :param text: the HTML to linkify.
        :returns: the linkified HTML.
        """
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
                if linkifiable and child.tag == "a" and self.process_existing:
                    self._process_existing_anchor(child)
                else:
                    nested = linkifiable and child.tag not in _NEVER_LINKIFY and child.tag not in self.skip_tags
                    self._walk(child, linkifiable=nested)

    def _process_existing_anchor(self, anchor: Element) -> None:
        """Run the callbacks over an ``<a>`` already in the input; unwrap it if one vetoes, else rewrite its attrs."""
        original_text = anchor.text
        href = anchor.attrs.get("href")
        attrs = {name: _attr_str(value) for name, value in anchor.attrs.items() if name != "href"}
        link = LinkCandidate(_attr_str(href), original_text, attrs, existing=True)
        for callback in self.callbacks:
            result = callback(link)
            if result is None:
                anchor.unwrap()
                return
            link = result
        merged: dict[str, str] = {"href": link.url} if link.url else {}
        merged.update(link.attrs)
        anchor_attrs = anchor.attrs
        for name in list(anchor_attrs):
            if name not in merged:
                del anchor_attrs[name]
        for name, value in merged.items():
            anchor_attrs[name] = value
        if link.text != original_text:
            anchor.clear()
            anchor.append(Text(link.text))

    def _linkify_text(self, node: Text) -> None:
        """Replace a text node with the text and anchors that the link spans in it imply."""
        data = node.data
        spans = _linkify_scan(data, self.parse_email, True, self.extra_tlds, self.url_schemes)  # ruff:ignore[boolean-positional-value-in-call]  # True enables bare domains
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
        elif _SCHEME.match(matched):  # the scanner already gated the scheme; a match here keeps its own scheme
            url = matched
        else:
            url = "http://" + matched
        if len(self.callbacks) == 1 and self.callbacks[0] is nofollow:
            attrs = {"href": url}
            if _is_web_url(url):
                attrs["rel"] = "nofollow"
            return Element("a", attrs, [Text(matched)])
        link = LinkCandidate(url, matched)
        for callback in self.callbacks:
            result = callback(link)
            if result is None:
                return None
            link = result
        anchor = Element("a", {"href": link.url, **link.attrs})
        anchor.append(Text(link.text))
        return anchor


def linkify(text: str, options: Linkify | None = None) -> str:
    """
    Find URLs and email addresses in HTML and wrap them in ``<a>`` links, leaving existing markup untouched.

    :param text: the HTML to linkify.
    :param options: the configuration to apply; None uses ``DEFAULT_CALLBACKS`` and detects nothing else.
    :returns: the linkified HTML.
    """
    return Linker(options).linkify(text)


class LinkSpan:
    """
    One URL or email address found in a run of plain text.

    :param start: the half-open start offset of the match in the scanned text.
    :param end: the half-open end offset of the match in the scanned text.
    :param text: the matched substring exactly as it appeared.
    :param url: the normalized ``href`` (``mailto:`` for an email, ``http://`` for a bare domain, the text
        itself for a ``scheme://`` or registered scheme-less URL).
    :param is_email: whether the match is an email address.
    """

    __slots__ = ("end", "is_email", "start", "text", "url")

    def __init__(self, start: int, end: int, text: str, url: str, is_email: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
        """Create a link span."""
        self.start = start
        self.end = end
        self.text = text
        self.url = url
        self.is_email = is_email

    def __repr__(self) -> str:
        """Render the span with its offsets and url, the way a debugger or a failing test wants to see it."""
        return f"LinkSpan(start={self.start}, end={self.end}, text={self.text!r}, url={self.url!r})"

    def __eq__(self, other: object) -> bool:
        """Two spans are equal when every field matches; comparing to a non-span defers to the other operand."""
        if not isinstance(other, LinkSpan):
            return NotImplemented
        return (self.start, self.end, self.text, self.url, self.is_email) == (
            other.start,
            other.end,
            other.text,
            other.url,
            other.is_email,
        )

    __hash__ = None  # a span carries offsets into one specific string, so it is not a stable dict key


def _span_from_match(text: str, start: int, end: int, kind: int) -> LinkSpan:
    """Normalize one matched span into a :class:`LinkSpan`, adding the ``mailto:``/``http://`` scheme it needs."""
    matched = text[start:end]
    if kind == _EMAIL_KIND:
        url = "mailto:" + matched
    elif kind == _SCHEME_KIND or _SCHEME.match(matched):
        url = matched
    else:
        url = "http://" + matched
    return LinkSpan(start, end, matched, url, kind == _EMAIL_KIND)


class LinkDetector:
    """
    Find the links in plain text, configured once and reused per call.

    Unlike :class:`Linker`, which rewrites HTML, a detector only *locates* links and hands back :class:`LinkSpan`
    objects, leaving the text untouched.

    :param emails: detect bare email addresses.
    :param bare_domains: detect bare domains (``example.com``) with no explicit scheme.
    :param tlds: custom top-level domains accepted for bare-domain matching, on top of the IANA table.
    :param schemes: extra schemes to detect, both as scheme-less opaque URLs (``tel:``, ``bitcoin:``) and as
        ``scheme://`` authority URLs, on top of the built-in ``http``/``https``/``ftp`` set; an unregistered scheme
        such as ``javascript://`` or a typo like ``hppt://`` is not detected.
    """

    def __init__(
        self,
        *,
        emails: bool = True,
        bare_domains: bool = True,
        tlds: Iterable[str] = (),
        schemes: Iterable[str] = (),
    ) -> None:
        """Build a reusable detector."""
        self.emails = emails
        self.bare_domains = bare_domains
        self._tlds = tuple({tld.lower().removeprefix(".") for tld in tlds})
        self._schemes = tuple({scheme.lower().rstrip(":") for scheme in schemes})
        self._url_schemes = tuple(sorted(set(_DEFAULT_URL_SCHEMES).union(self._schemes)))

    def find(self, text: str) -> list[LinkSpan]:
        """
        Find every link in a run of text.

        :param text: the text to scan.
        :returns: every link as a :class:`LinkSpan`, in the order it appears.
        """
        spans = _linkify_find(text, self.emails, self.bare_domains, self._tlds, self._schemes, self._url_schemes)
        return [_span_from_match(text, start, end, kind) for start, end, kind in spans]

    def has_link(self, text: str) -> bool:
        """
        Test a run of text for any link, cheaper than :meth:`find` when only presence matters.

        :param text: the text to scan.
        :returns: whether the text contains at least one link.
        """
        return bool(_linkify_find(text, self.emails, self.bare_domains, self._tlds, self._schemes, self._url_schemes))


__all__ = [
    "DEFAULT_CALLBACKS",
    "Callback",
    "LinkCandidate",
    "LinkDetector",
    "LinkSpan",
    "Linker",
    "Linkify",
    "linkify",
    "nofollow",
    "target_blank",
]
