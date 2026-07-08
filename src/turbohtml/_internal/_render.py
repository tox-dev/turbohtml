"""
Configuration objects for the tree's output renderers.

``to_markdown``, ``to_text``/``to_annotated_text`` and ``serialize``/``encode`` each carried a long flat keyword list
that was hard to read, hard to document, and let mutually exclusive options (markdown's ``strip`` vs ``convert``) be
passed together. This module replaces those keyword walls with three immutable, thread-safe configuration objects --
:class:`Markdown`, :class:`PlainText` and :class:`Html` -- mirroring the :class:`~turbohtml.Policy` sanitizer config and
the :class:`~turbohtml.Indent`/:class:`~turbohtml.Minify` layout objects already in the API.

The markdown surface is large enough that a single flat config would just move the wall, so :class:`Markdown` groups its
knobs into themed sub-configs (``Markdown.Headings``, ``Markdown.Links``, ...). Each config validates itself on
construction and exposes a private ``_unpack`` that yields only the values differing from the renderer's built-in
defaults, so an empty config reproduces the no-argument rendering exactly and the C renderer keeps its existing
default handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Final, Literal

from turbohtml._html import Formatter, Indent, Minify, _register_render_configs

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from turbohtml._html import Element


@dataclass(frozen=True)
class Markdown:
    """
    How :meth:`Node.to_markdown` renders a subtree as Markdown.

    Build one and reuse it across threads; every field has a GitHub-Flavored-Markdown default, so ``Markdown()``
    reproduces the no-argument rendering. The many knobs are grouped into themed sub-configs to keep any one object
    small -- ``Markdown(links=Markdown.Links(style="reference"))`` rather than a flat ``link_style="reference"``.

    :param headings: how headings are written (ATX, closed ATX, or setext).
    :param lists: the unordered-list markers.
    :param inline: inline emphasis, strike, sub/sup, and quote wrappers.
    :param code: fenced vs indented code blocks and their language.
    :param links: link style and which links are kept.
    :param images: how images render and their fallback alt text.
    :param tables: table rendering and header detection.
    :param escaping: which Markdown-significant characters are escaped.
    :param wrapping: word-wrapping of prose and constructs.
    :param document: line breaks, block spacing, trimming, and transliteration.
    :param google: reading inline-CSS styling the way a Google Docs export encodes it.
    :param strip: tags rendered as their text only, dropping the markup; mutually exclusive with ``convert``.
    :param convert: the only tags to render as Markdown, every other tag dropped to text; mutually exclusive with
        ``strip``.
    :param converters: per-tag overrides, each a callable receiving the element and its rendered child Markdown.
    :raises ValueError: if both ``strip`` and ``convert`` are given, since they are mutually exclusive.
    """

    @dataclass(frozen=True)
    class Headings:
        """
        How headings are written.

        :param style: ``atx`` (``# h``), ``atx_closed`` (``# h #``), or ``setext`` (underlined).
        """

        style: Literal["atx", "atx_closed", "setext"] = "atx"

    @dataclass(frozen=True)
    class Lists:
        """
        How unordered lists are marked.

        :param bullets: unordered-list markers, cycled by nesting depth (e.g. ``-*+``).
        """

        bullets: str = "-"

    @dataclass(frozen=True)
    class Inline:
        """
        Inline text formatting: emphasis, sub/sup, and quote wrappers.

        :param strong: the bold wrapper.
        :param emphasis: the italic wrapper.
        :param strikethrough: ``keep`` wraps struck text, ``hide`` drops it.
        :param ignore_emphasis: render bold/italic/strike content as plain text, no markup.
        :param sub: the ``<sub>`` wrapper (empty drops the markup).
        :param sup: the ``<sup>`` wrapper (empty drops the markup).
        :param hide_strikethrough: in ``google`` mode, drop text a CSS line-through struck.
        :param quote_open: the opening ``<q>`` wrapper.
        :param quote_close: the closing ``<q>`` wrapper.
        """

        strong: str = "**"
        emphasis: str = "*"
        strikethrough: Literal["keep", "hide"] = "keep"
        ignore_emphasis: bool = False
        sub: str = ""
        sup: str = ""
        hide_strikethrough: bool = False
        quote_open: str = '"'
        quote_close: str = '"'

    @dataclass(frozen=True)
    class Code:
        """
        How code blocks are rendered.

        :param block_style: ``fenced`` (triple-backtick) or ``indented`` (four-space).
        :param language: default fence language when the element declares none.
        :param mark: wrap inline code in ``[code]``/``[/code]`` markers.
        """

        block_style: Literal["fenced", "indented"] = "fenced"
        language: str = ""
        mark: bool = False

    @dataclass(frozen=True)
    class Links:
        """
        Link style and which links are kept.

        :param style: ``inline`` (``[t](url)``) or ``reference`` (collected at the end).
        :param autolink: emit ``<url>`` when the text equals an absolute href.
        :param title: use the href as the title when none is given.
        :param ignore: render link text only, no markup.
        :param skip_internal: drop ``href="#..."`` fragment links.
        :param base_url: prefix resolved onto a relative link or image href.
        """

        style: Literal["inline", "reference"] = "inline"
        autolink: bool = True
        title: bool = False
        ignore: bool = False
        skip_internal: bool = False
        base_url: str = ""

    @dataclass(frozen=True)
    class Images:
        """
        How images are rendered.

        :param mode: ``markdown`` (``![alt](src)``), ``alt`` (alt text only), ``ignore``, or ``html`` (raw ``<img>``).
        :param default_alt: alt text used for an image that declares none.
        """

        mode: Literal["markdown", "alt", "ignore", "html"] = "markdown"
        default_alt: str = ""

    @dataclass(frozen=True)
    class Tables:
        """
        How tables are rendered.

        :param mode: ``markdown`` (pipe table), ``strip`` (cell text only), or ``html`` (raw ``<table>``).
        :param header: which row is the header: ``first``, ``detect``, or ``none``.
        :param pad: align columns to a common width.
        """

        mode: Literal["markdown", "strip", "html"] = "markdown"
        header: Literal["first", "detect", "none"] = "first"
        pad: bool = False

    @dataclass(frozen=True)
    class Escaping:
        """
        Which Markdown-significant characters are escaped.

        :param mode: ``minimal`` escapes only what a parser needs, ``all`` escapes every Markdown character.
        :param asterisks: escape literal ``*`` so it is not read as emphasis.
        :param underscores: escape literal ``_`` so it is not read as emphasis.
        """

        mode: Literal["minimal", "all"] = "minimal"
        asterisks: bool = True
        underscores: bool = True

    @dataclass(frozen=True)
    class Wrapping:
        """
        Word-wrapping of prose and constructs.

        :param width: word-wrap column for prose, or 0 to leave lines unwrapped.
        :param list_items: extend word wrapping into list-item text.
        :param links: allow a link or image construct to break across a wrap.
        """

        width: int = 0
        list_items: bool = False
        links: bool = True

    @dataclass(frozen=True)
    class Document:
        """
        Document-level layout: line breaks, block spacing, trimming, and transliteration.

        :param line_break: render a hard break as trailing ``spaces`` or a ``backslash``.
        :param block_spacing: a ``double`` blank line between blocks, or a ``single`` newline.
        :param trim: trim document edges: ``strip``, ``lstrip``, ``rstrip``, or ``none``.
        :param transliterate: fold common non-ASCII typography in prose to ASCII.
        """

        line_break: Literal["spaces", "backslash"] = "spaces"
        block_spacing: Literal["double", "single"] = "double"
        trim: Literal["strip", "lstrip", "rstrip", "none"] = "strip"
        transliterate: bool = False

    @dataclass(frozen=True)
    class GoogleDoc:
        """
        Reading inline-CSS styling the way a Google Docs export encodes it.

        :param enabled: read inline-CSS styling the way a Google Docs HTML export encodes it.
        :param list_indent: px of ``margin-left`` per list-nesting level (at least 1).
        """

        enabled: bool = False
        list_indent: int = 36

    headings: Markdown.Headings = field(default_factory=Headings)
    lists: Markdown.Lists = field(default_factory=Lists)
    inline: Markdown.Inline = field(default_factory=Inline)
    code: Markdown.Code = field(default_factory=Code)
    links: Markdown.Links = field(default_factory=Links)
    images: Markdown.Images = field(default_factory=Images)
    tables: Markdown.Tables = field(default_factory=Tables)
    escaping: Markdown.Escaping = field(default_factory=Escaping)
    wrapping: Markdown.Wrapping = field(default_factory=Wrapping)
    document: Markdown.Document = field(default_factory=Document)
    google: Markdown.GoogleDoc = field(default_factory=GoogleDoc)
    strip: Iterable[str] | None = None
    convert: Iterable[str] | None = None
    converters: Mapping[str, Callable[[Element, str], str]] | None = None

    def __post_init__(self) -> None:
        """Reject the one mutually exclusive pair up front, so the renderer never has to."""
        if self.strip is not None and self.convert is not None:
            msg = "strip and convert are mutually exclusive"
            raise ValueError(msg)

    @classmethod
    def google_doc(cls) -> Markdown:
        """
        Read a Google Docs HTML export: inline-CSS styling drives emphasis, and struck text is dropped.

        :returns: a config tuned for Google Docs export markup.
        """
        return cls(
            inline=cls.Inline(hide_strikethrough=True),
            google=cls.GoogleDoc(enabled=True),
        )

    def _unpack(self) -> dict[str, object]:
        """Flatten to the renderer's keyword names, emitting only values that differ from its defaults."""
        default = _MARKDOWN_DEFAULT
        flat = {
            "heading_style": (self.headings.style, default.headings.style),
            "bullets": (self.lists.bullets, default.lists.bullets),
            "strong": (self.inline.strong, default.inline.strong),
            "emphasis": (self.inline.emphasis, default.inline.emphasis),
            "strikethrough": (self.inline.strikethrough, default.inline.strikethrough),
            "ignore_emphasis": (self.inline.ignore_emphasis, default.inline.ignore_emphasis),
            "sub_symbol": (self.inline.sub, default.inline.sub),
            "sup_symbol": (self.inline.sup, default.inline.sup),
            "hide_strikethrough": (self.inline.hide_strikethrough, default.inline.hide_strikethrough),
            "quote_open": (self.inline.quote_open, default.inline.quote_open),
            "quote_close": (self.inline.quote_close, default.inline.quote_close),
            "code_block_style": (self.code.block_style, default.code.block_style),
            "code_language": (self.code.language, default.code.language),
            "mark_code": (self.code.mark, default.code.mark),
            "link_style": (self.links.style, default.links.style),
            "autolink": (self.links.autolink, default.links.autolink),
            "link_title": (self.links.title, default.links.title),
            "ignore_links": (self.links.ignore, default.links.ignore),
            "skip_internal_links": (self.links.skip_internal, default.links.skip_internal),
            "base_url": (self.links.base_url, default.links.base_url),
            "image_mode": (self.images.mode, default.images.mode),
            "default_image_alt": (self.images.default_alt, default.images.default_alt),
            "table_mode": (self.tables.mode, default.tables.mode),
            "table_header": (self.tables.header, default.tables.header),
            "pad_tables": (self.tables.pad, default.tables.pad),
            "escape_mode": (self.escaping.mode, default.escaping.mode),
            "escape_asterisks": (self.escaping.asterisks, default.escaping.asterisks),
            "escape_underscores": (self.escaping.underscores, default.escaping.underscores),
            "wrap_width": (self.wrapping.width, default.wrapping.width),
            "wrap_list_items": (self.wrapping.list_items, default.wrapping.list_items),
            "wrap_links": (self.wrapping.links, default.wrapping.links),
            "line_break": (self.document.line_break, default.document.line_break),
            "block_spacing": (self.document.block_spacing, default.document.block_spacing),
            "document_strip": (self.document.trim, default.document.trim),
            "transliterate": (self.document.transliterate, default.document.transliterate),
            "google_doc": (self.google.enabled, default.google.enabled),
            "google_list_indent": (self.google.list_indent, default.google.list_indent),
        }
        unpacked: dict[str, object] = {name: value for name, (value, base) in flat.items() if value != base}
        # pass the tag filters through unchanged: the renderer rejects a bare str (so
        # ``strip="div"`` is an error, not "strip tags d, i, v") and accepts any other iterable
        if self.strip is not None:
            unpacked["strip"] = self.strip
        if self.convert is not None:
            unpacked["convert"] = self.convert
        if self.converters is not None:
            unpacked["converters"] = self.converters
        return unpacked


_MARKDOWN_DEFAULT: Final = Markdown()


@dataclass(frozen=True)
class PlainText:
    """
    How :meth:`Node.to_text` and :meth:`Node.to_annotated_text` render a subtree as layout-aware plain text.

    :param width: wrap column for prose, or 0 to leave lines unwrapped.
    :param links: how to render ``<a>`` targets: ``none`` drops them, ``inline`` shows them after the text,
        ``footnote`` collects them at the end.
    :param images: render image alt text instead of skipping images.
    :param layout: ``extended`` adds blank lines and indentation; ``strict`` keeps the output compact.
    :param default_image_alt: alt text used for an image that declares none.
    :param table_cell_separator: string placed between table cells on a row.
    :param bullet: marker prefixed to each list item.
    """

    width: int = 0
    links: Literal["none", "inline", "footnote"] = "none"
    images: bool = False
    layout: Literal["extended", "strict"] = "extended"
    default_image_alt: str = ""
    table_cell_separator: str = "  "
    bullet: str = "* "

    def _unpack(self) -> dict[str, object]:
        """Flatten to the renderer's keyword names, emitting only values that differ from its defaults."""
        default = _PLAINTEXT_DEFAULT
        return {
            field_.name: value
            for field_ in fields(self)
            if (value := getattr(self, field_.name)) != getattr(default, field_.name)
        }


_PLAINTEXT_DEFAULT: Final = PlainText()


@dataclass(frozen=True)
class Html:
    """
    How :meth:`Node.serialize` and :meth:`Node.encode` render a subtree as HTML.

    :param formatter: the escape policy for text and attribute values.
    :param layout: ``None`` emits the compact WHATWG form, an :class:`~turbohtml.Indent` pretty-prints, a
        :class:`~turbohtml.Minify` minifies.
    :param sort_attributes: emit each element's attributes in name order rather than source order.
    :param meta_charset: normalize (or inject) the ``<meta charset>`` declaration to the output encoding.
    :param xml: emit XML/XHTML syntax -- every empty element self-closes (``<br/>``), foreign SVG and MathML
        subtrees carry their namespace declarations, and text and attribute values follow the XML escaping rules.
        The HTML void-element and raw-text special casing (and the ``formatter``/``meta_charset`` options) do not
        apply; a :class:`~turbohtml.Minify` layout stays HTML.
    """

    formatter: Formatter = Formatter.WHATWG
    layout: Indent | Minify | None = None
    sort_attributes: bool = False
    meta_charset: bool = False
    xml: bool = False

    def _unpack(self) -> dict[str, object]:
        """Flatten to the renderer's keyword names, emitting only values that differ from its defaults."""
        default = _HTML_DEFAULT
        return {
            field_.name: value
            for field_ in fields(self)
            if (value := getattr(self, field_.name)) != getattr(default, field_.name)
        }


_HTML_DEFAULT: Final = Html()


@dataclass(frozen=True)
class Canonical:
    """
    How :meth:`Node.canonicalize` renders a subtree as Canonical XML (c14n).

    Canonical XML is the byte-exact serialization an XML signature signs: attributes are reordered, redundant
    namespace declarations are dropped, empty elements are written as start-end pairs, and character references are
    normalized. turbohtml canonicalizes a complete subtree, so ``1.0`` and ``1.1`` produce identical bytes here bar the
    apex's inherited ``xml:`` attributes (``1.1`` keeps ``xml:id`` local).

    :param version: ``1.0`` (`Canonical XML 1.0 <https://www.w3.org/TR/xml-c14n>`_) or ``1.1``
        (`Canonical XML 1.1 <https://www.w3.org/TR/xml-c14n11/>`_).
    :param exclusive: use `Exclusive XML Canonicalization <https://www.w3.org/TR/xml-exc-c14n>`_, which renders only
        the namespaces a subtree visibly uses rather than inheriting every in-scope ancestor declaration.
    :param with_comments: keep comment nodes, the with-comments variant of each algorithm.
    :param inclusive_ns_prefixes: in exclusive mode, namespace prefixes promoted to the apex even when unused, so a
        detached subtree keeps them; only meaningful with ``exclusive``.
    :raises ValueError: if ``exclusive`` is combined with ``version="1.1"`` (exclusive c14n builds on 1.0 only), or if
        ``inclusive_ns_prefixes`` is given without ``exclusive``.
    """

    version: Literal["1.0", "1.1"] = "1.0"
    exclusive: bool = False
    with_comments: bool = False
    inclusive_ns_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject the option pairs the c14n algorithms leave undefined, so the renderer never has to."""
        if self.exclusive and self.version == "1.1":
            msg = "exclusive canonicalization is defined only over version 1.0"
            raise ValueError(msg)
        if self.inclusive_ns_prefixes and not self.exclusive:
            msg = "inclusive_ns_prefixes applies only in exclusive mode"
            raise ValueError(msg)

    def _unpack(self) -> dict[str, object]:
        """Flatten to the renderer's keyword names, emitting only values that differ from its defaults."""
        default = _CANONICAL_DEFAULT
        return {
            field_.name: value
            for field_ in fields(self)
            if (value := getattr(self, field_.name)) != getattr(default, field_.name)
        }


_CANONICAL_DEFAULT: Final = Canonical()

_register_render_configs(Markdown, PlainText, Html, Canonical)


__all__ = [
    "Canonical",
    "Html",
    "Markdown",
    "PlainText",
]
