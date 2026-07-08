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

import re
from dataclasses import dataclass, field
from enum import Enum
from itertools import starmap
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from ._html import _sanitize, parse_fragment

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ._html import Element


@dataclass(frozen=True, slots=True)
class Removed:
    """
    One item a policy dropped, the shape :meth:`Sanitizer.sanitize_report` returns.

    :param tag: the tag name of the dropped element, or of the element a dropped attribute was on.
    :param attribute: the dropped attribute's name, or ``None`` when the element itself was dropped.
    """

    tag: str
    attribute: str | None = None


@dataclass(frozen=True, slots=True)
class Transform:
    """
    A rename rule for ``Policy.transform_tags``, sanitize-html's ``simpleTransform``.

    Mapping a source tag to a bare string renames it; mapping it to a ``Transform`` renames it and adds attributes. The
    added attributes are not trusted: they join the element's own attributes and go through the same allowlist and
    URL/style scrubbing, so a transform cannot add an attribute the policy would otherwise strip.

    :param tag: the target tag name to rename to.
    :param attributes: attribute name/value pairs to add to the renamed element (added if absent, overwritten if
        present).
    """

    tag: str
    attributes: Mapping[str, str] = field(default_factory=dict)


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
DEFAULT_TAGS: Final = frozenset({
    "a",
    "abbr",
    "acronym",
    "b",
    "blockquote",
    "code",
    "em",
    "i",
    "li",
    "ol",
    "strong",
    "ul",
})
#: bleach's default allowed attributes, keyed by tag (``"*"`` would match every tag).
DEFAULT_ATTRIBUTES: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    "a": frozenset({"href", "title"}),
    "abbr": frozenset({"title"}),
    "acronym": frozenset({"title"}),
})
#: bleach's default URL schemes.
DEFAULT_SCHEMES: Final = frozenset({"http", "https", "mailto"})
#: bleach's default allowed CSS properties (CSS 2.1 safe set plus SVG paint), for scrubbing a ``style`` attribute.
DEFAULT_CSS_PROPERTIES: Final = frozenset({
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
_RELAXED_TAGS: Final = DEFAULT_TAGS | {
    "p", "br", "hr", "span", "div", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
    "dl", "dt", "dd", "sub", "sup", "del", "ins", "mark", "small", "u", "s", "q", "cite", "img",
    "figure", "figcaption", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
}  # fmt: skip
_RELAXED_ATTRIBUTES: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
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
    :param transform_tags: rename rules applied before the allowlist, keyed by source tag (sanitize-html's
        ``transformTags``). A value that is a plain string renames the element to it; a :class:`Transform` renames it
        and adds attributes. The rename runs first, then the renamed element is re-checked from scratch, so a transform
        decides an element's *name* but never its safety: the allowlist still governs the target tag (mapping to
        ``script`` still drops it), and any added attributes are scrubbed like the element's own. Only HTML elements are
        transformed, matched by tag name. Empty (the default) renames nothing.
    :param allowed_styles: a per-property value allowlist for the ``style`` attribute, keyed
        ``{tag: {property: [pattern, ...]}}`` with ``"*"`` as a tag matching every element (sanitize-html's
        ``allowedStyles``). A declaration survives only when its property is listed for the element's tag or ``"*"``
        (their pattern lists union) and its value matches one of the patterns (an unanchored :func:`re.search`).
        Patterns are strings or precompiled :class:`re.Pattern`. This narrows on top of ``css_properties`` and the
        non-configurable dangerous-value baseline: a property this admits is still dropped unless ``css_properties``
        lists it too, and ``expression()`` or a ``url()`` with a disallowed scheme is dropped even if a pattern would
        match it. Empty (the default) leaves ``css_properties`` as the only ``style`` filter.
    :param media_hosts: allowed hosts for an embedded-media ``src`` (``audio``, ``video``, ``source``, ``track``); a
        ``src`` whose URL host is not one of these lowercase entries is dropped. Empty means no host restriction.
    :param strip_template_markers: collapse template-engine expressions (``{{ }}``, ``${ }``, ``<% %>``) in kept text
        and attribute values to a single space, so sanitized output cannot re-inject when a template engine (Angular,
        Vue, Mustache, EJS, ERB) later renders it. DOMPurify's ``SAFE_FOR_TEMPLATES``; off by default.
    :param isolate_named_props: prefix every kept ``id`` and ``name`` value with ``user-content-`` so it cannot shadow
        a built-in ``document`` or form property through named access (DOM clobbering: ``<input name="attributes">``
        makes ``form.attributes`` resolve to the input, ``<img name="body">`` hides ``document.body``). The prefix is
        left alone when already present, so re-sanitizing is a fixpoint. DOMPurify's ``SANITIZE_NAMED_PROPS``; off by
        default.
    :param custom_element_check: a predicate that keeps an unlisted custom element (a hyphenated HTML tag such as
        ``my-widget`` or ``x-card``) without naming it in ``tags``, DOMPurify's
        ``CUSTOM_ELEMENT_HANDLING.tagNameCheck``. Given the element's lowercased tag name, return true to keep it (pass
        ``re.compile(r"x-.*").search`` to drive it from a regex). Only basic custom-element names reach it (the reserved
        ``annotation-xml``/``font-face`` family never does), and the safety baseline still escapes unsafe tags and
        scrubs ``on*``/URL/style attributes on whatever it keeps. ``None`` (the default) escapes every unlisted tag as
        before.
    :param custom_attribute_check: a predicate that keeps an unlisted attribute on a kept custom element, DOMPurify's
        ``CUSTOM_ELEMENT_HANDLING.attributeNameCheck``. Given ``(tag, attribute_name)`` return true to keep it; the
        ``on*``, URL-scheme, and ``style`` scrubbing still apply, so this widens the name allowlist, never the safety
        baseline. ``None`` (the default) keeps only the attributes ``attributes`` already admits.
    :param allow_customized_builtins: keep an ``is`` attribute whose value passes ``custom_element_check``, so a
        customized built-in element (``<button is="my-button">``) survives, DOMPurify's
        ``allowCustomizedBuiltInElements``. Off by default, and inert without ``custom_element_check``.
    :param allow_html: keep HTML-namespace elements; turning it off drops the whole HTML namespace, DOMPurify's
        ``USE_PROFILES.html``. Independent of the tag allowlist, so it composes with ``allow_svg``/``allow_mathml`` to
        select which content languages a policy admits.
    :param allow_svg: keep SVG-namespace elements, DOMPurify's ``USE_PROFILES.svg``. Off drops every SVG element even
        when its tag is in ``tags``.
    :param allow_mathml: keep MathML-namespace elements, DOMPurify's ``USE_PROFILES.mathMl``. Off drops every MathML
        element even when its tag is in ``tags``.
    :param xml: emit well-formed XML/XHTML instead of HTML, DOMPurify's ``RETURN_DOM`` served through the XML
        serializer. Every kept empty element self-closes (``<br/>``), foreign SVG and MathML subtrees carry their
        namespace declarations, text and attribute values follow the XML escaping rules, and a kept comment or a
        stray control character is neutralized so the result reparses through :func:`turbohtml.parse_xml`. Use it to
        clean an XHTML dialect (Reportlab's RML, ePub content) whose consumer rejects HTML's bare ``<br>``. Off (the
        default) keeps the HTML serialization.
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
    strip_template_markers: bool = False
    allowed_styles: Mapping[str, Mapping[str, Sequence[str | re.Pattern[str]]]] = field(default_factory=dict)
    transform_tags: Mapping[str, Transform | str] = field(default_factory=dict)
    isolate_named_props: bool = False
    custom_element_check: Callable[[str], bool] | None = None
    custom_attribute_check: Callable[[str, str], bool] | None = None
    allow_customized_builtins: bool = False
    allow_html: bool = True
    allow_svg: bool = True
    allow_mathml: bool = True
    xml: bool = False

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
        self._allowed_styles = {
            tag: {
                prop.lower(): tuple(p if isinstance(p, re.Pattern) else re.compile(p) for p in patterns)
                for prop, patterns in props.items()
            }
            for tag, props in self.policy.allowed_styles.items()
        }
        self._transform_tags = dict(starmap(self._compile_transform, self.policy.transform_tags.items()))

    @staticmethod
    def _compile_transform(source: str, target: Transform | str) -> tuple[str, tuple[str, dict[str, str]]]:
        """Normalize one transform rule into ``(source, (target_tag, added_attributes))`` for the C walk."""
        if isinstance(target, str):
            name, attributes = target, {}
        elif isinstance(target, Transform):
            name, attributes = target.tag, dict(target.attributes)
        else:
            msg = f"transform_tags[{source!r}] must be a str or Transform, got {type(target).__name__}"
            raise TypeError(msg)
        if not name:
            msg = f"transform_tags[{source!r}] target tag must be a non-empty string"
            raise ValueError(msg)
        return source, (name, attributes)

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
        return self._render(self._filter(html, None))

    def sanitize_report(self, html: str) -> tuple[str, list[Removed]]:
        """
        Sanitize a fragment and report what the policy dropped, the way DOMPurify populates ``DOMPurify.removed``.

        Every disallowed element (removed, stripped, or escaped) and every stripped attribute becomes one
        :class:`Removed` record, in the order the walk reached it, so a caller can log or tune a policy against
        evidence instead of guessing.

        :param html: the untrusted HTML fragment.
        :returns: the sanitized HTML paired with the list of dropped items.
        :raises TypeError: like :meth:`sanitize`, on a mistyped set-valued policy field.
        :raises ValueError: like :meth:`sanitize`, on an empty ``attribute_prefixes`` entry.
        """
        removed: list[tuple[str, str | None]] = []
        html_out = self._render(self._filter(html, removed))
        return html_out, list(starmap(Removed, removed))

    def _render(self, root: Element) -> str:
        """Serialize the sanitized root's children as XML when the policy asks, else as HTML."""
        return root.inner_xml if self.policy.xml else root.inner_html

    def _filter(self, html: str, removed: list[tuple[str, str | None]] | None) -> Element:
        """Run the C walk over a freshly parsed fragment, appending drops to ``removed`` when it is not None."""
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
            policy.strip_template_markers,
            removed,
            self._allowed_styles,
            self._transform_tags,
            policy.isolate_named_props,
            policy.custom_element_check,
            policy.custom_attribute_check,
            policy.allow_customized_builtins,
            policy.allow_html,
            policy.allow_svg,
            policy.allow_mathml,
        )
        return root


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


def sanitize_report(html: str, options: Policy | None = None) -> tuple[str, list[Removed]]:
    """
    Sanitize a fragment and report what the policy dropped, like DOMPurify's ``DOMPurify.removed``.

    :param html: the untrusted HTML fragment.
    :param options: the policy to enforce; None uses bleach's default allowlist.
    :returns: the sanitized HTML paired with one :class:`Removed` record per dropped element or attribute.
    :raises TypeError: like :func:`sanitize`, on a mistyped set-valued policy field.
    :raises ValueError: like :func:`sanitize`, on an empty ``attribute_prefixes`` entry.
    """
    return Sanitizer(options).sanitize_report(html)


__all__ = [
    "DEFAULT_ATTRIBUTES",
    "DEFAULT_CSS_PROPERTIES",
    "DEFAULT_SCHEMES",
    "DEFAULT_TAGS",
    "OnDisallowed",
    "Policy",
    "Removed",
    "Sanitizer",
    "Transform",
    "sanitize",
    "sanitize_report",
]
