"""
Sanitize untrusted HTML against an allowlist, the way bleach.clean did.

bleach is the last maintained Python sanitizer and it is end of life, deprecated for sitting on the unmaintained
html5lib; turbohtml owns a maintained WHATWG tree builder, so this rebuilds the sanitizer on it. The model is the one
every safe sanitizer converged on: parse the input into a real tree, drop everything not on the allowlist while walking
it, and serialize once. There is no serialize-then-reparse round trip, which is the step that lets mutation XSS slip
back in, and a :class:`Policy` baseline removes ``<script>``, event-handler attributes, and ``javascript:`` URLs even
when a caller's allowlist would admit them, so a no-argument :func:`sanitize` is safe by construction.

This module is the policy facade only; the walk that keeps, escapes, strips, or removes each node runs in C
(``turbohtml._html._sanitize``), so the safety baseline lives below the configurable surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING

from ._html import _sanitize, parse_fragment

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class OnDisallowed(Enum):
    """
    What to do with an element the policy does not allow.

    ``ESCAPE`` renders the tag as visible text (``<x>`` becomes ``&lt;x&gt;``), the safe bleach default that keeps the
    content readable; ``STRIP`` drops the tag but keeps its children; ``REMOVE`` drops the tag and its whole subtree.
    """

    ESCAPE = 0
    STRIP = 1
    REMOVE = 2


#: bleach's default allowed tags, the migration baseline.
DEFAULT_TAGS = frozenset({"a", "abbr", "acronym", "b", "blockquote", "code", "em", "i", "li", "ol", "strong", "ul"})
#: bleach's default allowed attributes, keyed by tag (``"*"`` would match every tag).
DEFAULT_ATTRIBUTES: Mapping[str, frozenset[str]] = MappingProxyType({
    "a": frozenset({"href", "title"}),
    "abbr": frozenset({"title"}),
    "acronym": frozenset({"title"}),
})
#: bleach's default URL schemes.
DEFAULT_SCHEMES = frozenset({"http", "https", "mailto"})
#: bleach's default allowed CSS properties (CSS 2.1 safe set plus SVG paint), for scrubbing a ``style`` attribute.
DEFAULT_CSS_PROPERTIES = frozenset({
    "azimuth", "background-color", "border-bottom-color", "border-collapse", "border-color", "border-left-color",
    "border-right-color", "border-top-color", "clear", "color", "cursor", "direction", "display", "elevation",
    "float", "font", "font-family", "font-size", "font-style", "font-variant", "font-weight", "height",
    "letter-spacing", "line-height", "overflow", "pause", "pause-after", "pause-before", "pitch", "pitch-range",
    "richness", "speak", "speak-header", "speak-numeral", "speak-punctuation", "speech-rate", "stress", "text-align",
    "text-decoration", "text-indent", "unicode-bidi", "vertical-align", "voice-family", "volume", "white-space",
    "width", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
    "stroke-opacity",
})  # fmt: skip

# A roomier set for the relaxed preset: typical user-generated content (headings, tables, images, figures).
_RELAXED_TAGS = DEFAULT_TAGS | {
    "p", "br", "hr", "span", "div", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
    "dl", "dt", "dd", "sub", "sup", "del", "ins", "mark", "small", "u", "s", "q", "cite", "img",
    "figure", "figcaption", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
}  # fmt: skip
_RELAXED_ATTRIBUTES: Mapping[str, frozenset[str]] = MappingProxyType({
    "*": frozenset({"title", "lang", "dir"}),
    "a": frozenset({"href", "title", "name"}),
    "img": frozenset({"src", "alt", "title", "width", "height"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan", "scope"}),
    "col": frozenset({"span"}),
    "colgroup": frozenset({"span"}),
})


@dataclass(frozen=True)
class Policy:
    """
    An immutable, thread-safe description of what sanitizing keeps.

    Build one and reuse it across threads. Whatever a policy allows, a non-configurable baseline still removes the
    unsafe element set, event-handler attributes, and dangerous URL schemes, so every policy is safe by construction.

    :param tags: the allowed element set; any tag outside it is handled per ``on_disallowed_tag``.
    :param attributes: allowed attribute names keyed by tag (``"*"`` as the key matches every tag, and ``"*"`` inside a
        set allows every name).
    :param url_schemes: the allowlist for URL-bearing attributes such as ``href`` and ``src``.
    :param allow_relative_urls: keep relative (scheme-less) URLs, which carry no scheme to check.
    :param on_disallowed_tag: how to treat a tag not in ``tags`` (:class:`OnDisallowed`: escape, strip, or remove).
    :param strip_comments: drop HTML comments from the output.
    :param add_link_rel: ``rel`` tokens forced onto every kept ``<a href>`` (e.g. ``noopener``).
    :param attribute_filter: an optional last word over every surviving attribute, returning a replacement value or
        ``None`` to drop it.
    :param set_attributes: attribute values forced onto every kept instance of a tag (added if absent, overwritten if
        present); unlike ``attribute_filter``, this can add attributes that were not there.
    :param remove_with_content: disallowed tags whose whole subtree is dropped (e.g. ``script``/``style``) rather than
        escaped or stripped, so their text never leaks into the output.
    :param css_properties: the CSS property allowlist. A kept ``style`` attribute and, when ``style`` is in ``tags``,
        the ``<style>`` element's stylesheet body are both scrubbed against it: any declaration whose property name is
        not in the set (or whose value smuggles ``expression()`` or a ``url()`` with a disallowed scheme) is dropped,
        while selectors and block nesting are kept, so dangerous CSS cannot ride in on a kept ``style``.
    :param attribute_prefixes: allow any attribute whose name starts with one of these prefixes (e.g. ``"data-"`` for
        every ``data-*``), on top of the exact-name and ``"*"`` matches in ``attributes``.
    :param attribute_values: restrict a kept attribute to literal values, keyed ``{tag: {attribute: allowed_values}}``;
        a surviving attribute whose value is outside its set is dropped. This narrows an attribute ``attributes``
        already admits and cannot admit a new one.
    :param media_hosts: allowed hosts for an embedded-media ``src`` (``audio``, ``video``, ``source``, ``track``); a
        ``src`` whose URL host is not one of these lowercase entries is dropped. Empty means no host restriction.
    """

    tags: frozenset[str] = DEFAULT_TAGS
    # default_factory, not a plain default: dataclasses before 3.12 reject a MappingProxyType default as "mutable"
    attributes: Mapping[str, frozenset[str]] = field(default_factory=lambda: DEFAULT_ATTRIBUTES)
    url_schemes: frozenset[str] = DEFAULT_SCHEMES
    allow_relative_urls: bool = True
    on_disallowed_tag: OnDisallowed = OnDisallowed.ESCAPE
    strip_comments: bool = True
    add_link_rel: frozenset[str] = frozenset()
    attribute_filter: Callable[[str, str, str], str | None] | None = None
    set_attributes: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    remove_with_content: frozenset[str] = frozenset()
    css_properties: frozenset[str] = DEFAULT_CSS_PROPERTIES
    attribute_prefixes: frozenset[str] = frozenset()
    attribute_values: Mapping[str, Mapping[str, frozenset[str]]] = field(default_factory=dict)
    media_hosts: frozenset[str] = frozenset()

    @classmethod
    def strict(cls) -> Policy:
        """
        Allow no markup at all: every tag is escaped to text and every attribute dropped.

        :returns: the strict policy.
        """
        return cls(tags=frozenset(), attributes=MappingProxyType({}))

    @classmethod
    def basic(cls) -> Policy:
        """
        Allow bleach's default 12-tag set, for migration parity.

        :returns: the basic policy.
        """
        return cls()

    @classmethod
    def relaxed(cls) -> Policy:
        """
        Allow the richer set typical user-generated content needs: headings, tables, images, and figures.

        :returns: the relaxed policy.
        """
        return cls(
            tags=frozenset(_RELAXED_TAGS),
            attributes=_RELAXED_ATTRIBUTES,
            add_link_rel=frozenset({"noopener", "noreferrer"}),
        )


class Sanitizer:
    """
    A reusable sanitizer; build it once from a :class:`Policy` and call :meth:`sanitize` from any thread.

    :param options: the policy to enforce; None uses bleach's default allowlist.
    """

    def __init__(self, options: Policy | None = None) -> None:
        """Compile a policy into the form the C walk consumes."""
        self.policy = options if options is not None else Policy()
        self._attributes = dict(self.policy.attributes)
        self._link_rel = " ".join(sorted(self.policy.add_link_rel)) or None
        self._set_attributes = {tag: dict(values) for tag, values in self.policy.set_attributes.items()}
        self._attribute_values = {
            tag: {attr: frozenset(values) for attr, values in attrs.items()}
            for tag, attrs in self.policy.attribute_values.items()
        }

    def sanitize(self, html: str) -> str:
        """
        Sanitize an HTML fragment.

        :param html: the untrusted HTML fragment.
        :returns: the sanitized, safe HTML.
        :raises TypeError: if a set-typed policy field (``tags``, ``url_schemes``, ``remove_with_content``,
            ``css_properties``, ``attribute_prefixes``, ``media_hosts``) holds a value that is not a set or frozenset,
            or ``attribute_prefixes`` contains a non-string.
        :raises ValueError: if ``attribute_prefixes`` contains an empty string, which would match every attribute.
        """
        policy = self.policy
        root = parse_fragment(html)
        _sanitize(
            root,
            policy.tags,
            self._attributes,
            policy.url_schemes,
            policy.allow_relative_urls,
            policy.on_disallowed_tag.value,
            policy.strip_comments,
            self._link_rel,
            policy.attribute_filter,
            self._set_attributes,
            policy.remove_with_content,
            policy.css_properties,
            policy.attribute_prefixes,
            self._attribute_values,
            policy.media_hosts,
        )
        return root.inner_html


def sanitize(html: str, options: Policy | None = None) -> str:
    """
    Sanitize an HTML fragment against a policy.

    :param html: the untrusted HTML fragment.
    :param options: the policy to enforce; None uses bleach's default allowlist.
    :returns: the sanitized, safe HTML.
    :raises TypeError: if a set-typed policy field (``tags``, ``url_schemes``, ``remove_with_content``,
        ``css_properties``, ``attribute_prefixes``, ``media_hosts``) holds a value that is not a set or frozenset, or
        ``attribute_prefixes`` contains a non-string.
    :raises ValueError: if ``attribute_prefixes`` contains an empty string, which would match every attribute.
    """
    return Sanitizer(options).sanitize(html)


__all__ = [
    "DEFAULT_ATTRIBUTES",
    "DEFAULT_CSS_PROPERTIES",
    "DEFAULT_SCHEMES",
    "DEFAULT_TAGS",
    "OnDisallowed",
    "Policy",
    "Sanitizer",
    "sanitize",
]
