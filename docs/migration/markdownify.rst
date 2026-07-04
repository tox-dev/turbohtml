##################
 From markdownify
##################

.. package-meta:: markdownify matthewwithanm/python-markdownify

`markdownify <https://github.com/matthewwithanm/python-markdownify>`_ converts HTML to Markdown by walking a
BeautifulSoup tree. Its distinctive design is the ``MarkdownConverter`` class with a per-tag ``convert_<tag>`` override
system: subclass the converter, define a ``convert_a`` or ``convert_img`` method, and it replaces the built-in rendering
for that element. On top of that it exposes a flat set of keyword options (heading style, bullets, the shared strong/em
symbol, escaping toggles, autolinks, table header inference, line-break style, document trimming, and tag allow/deny
filters) and a command-line converter. It is a common choice for turning scraped or user-submitted HTML into Markdown in
content pipelines, documentation tooling, and chat/mail ingestion.

turbohtml covers the same conversion through :meth:`~turbohtml.Node.to_markdown`, a single fully typed method on the
parsed WHATWG tree. It walks the tree in C off the WHATWG parse instead of running a second Python walk over a
BeautifulSoup tree, and groups markdownify's flat options into named config dataclasses so each concept has one name.

**************************
 turbohtml vs markdownify
**************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - markdownify
    - - Scope
      - Full WHATWG HTML parser and tree with Markdown, plain-text, and HTML serialization
      - HTML-to-Markdown conversion over a BeautifulSoup tree
    - - Feature breadth
      - Same Markdown option surface (headings, lists, inline marks, links, images, tables, wrapping, code, escaping)
        plus per-tag converters, text extraction, selectors, and sanitization on the shared tree
      - Mature Markdown-conversion options, ``convert_<tag>`` subclass overrides, and a CLI
    - - Performance
      - C tree walk off the WHATWG parse, roughly two orders of magnitude faster on the benchmark below
      - Pure-Python walk over a BeautifulSoup tree the parser must build first
    - - Typing
      - Fully type annotated, config via frozen dataclasses
      - Untyped public API
    - - Dependencies
      - Compiled C extension (prebuilt wheels)
      - Pure Python, but requires ``beautifulsoup4`` and a bs4 parser
    - - Maintenance
      - Actively developed
      - Actively developed

Feature overlap
===============

Port these markdownify calls 1:1 onto :meth:`~turbohtml.Node.to_markdown` with a :class:`~turbohtml.Markdown` config:

- ``markdownify(text)`` -> ``turbohtml.parse(text).to_markdown()``.
- Heading style: ATX, closed ATX, or setext/underlined (``heading_style``).
- List bullets, cycled by nesting depth (``bullets``).
- Sub/superscript wrappers (``sub_symbol``, ``sup_symbol``).
- Escaping of asterisks, underscores, and miscellaneous Markdown characters (``escape_asterisks``,
  ``escape_underscores``, ``escape_misc``).
- Autolinks and href-as-title (``autolinks``, ``default_title``).
- Table header inference (``table_infer_header``).
- Line-break style and document trimming (``newline_style``, ``strip_document``).
- Default code-fence language (``code_language``).
- Tag allow/deny filters (``strip``, ``convert``) and per-tag output overrides (``convert_<tag>``).

What turbohtml adds
===================

- One parsed tree serves Markdown, layout-aware plain text (:meth:`~turbohtml.Node.to_text`), and HTML
  (:meth:`~turbohtml.Node.serialize`); markdownify is Markdown-only.
- Convert any subtree by calling :meth:`~turbohtml.Node.to_markdown` on a selected element
  (``doc.find("article").to_markdown()``) instead of pre-slicing the HTML string.
- Full spec-compliant WHATWG parsing feeds the conversion, so malformed markup is repaired the same way a browser does
  before it is rendered to Markdown, with no separate BeautifulSoup parser to pick or install.
- Independent strong and emphasis markers (``Markdown.Inline(strong=..., emphasis=...)``), a strict superset of
  markdownify's single ``strong_em_symbol`` that derives both from one character.
- Grouped, frozen-dataclass config (``Markdown.Links``, ``Markdown.Images``, ``Markdown.Tables`` ...) is fully typed and
  discoverable, versus markdownify's flat set of untyped keyword options.
- Reference-style links, image-render modes, transliteration, and a Google Docs inline-CSS mode
  (``Markdown.google_doc()``) that markdownify does not expose.

What markdownify has that turbohtml does not
============================================

- A command-line converter (``python -m markdownify`` / the ``markdownify`` console script). turbohtml exposes Markdown
  as a library method only; wrap ``turbohtml.parse(open(path).read()).to_markdown()`` in a short script for equivalent
  shell use.
- Subclass-based extension: markdownify lets a ``MarkdownConverter`` subclass override any ``convert_<tag>`` method and
  call the parent implementation with ``super()``. turbohtml's ``Markdown(converters=...)`` maps a tag name to a
  callable receiving the element and its already-rendered child Markdown, which covers per-tag replacement but not
  ``super()`` chaining onto the built-in renderer.
- ``keep_inline_images_in`` restricts which parent tags keep an inline image; turbohtml's ``Markdown.Images(mode=...)``
  is document-wide, with no per-parent-tag equivalent.
- ``bs4_options`` for parser selection. turbohtml always runs the WHATWG algorithm, so there is no parser to configure.

Performance
===========

.. bench-table::
    :file: bench/markdownify.json

****************
 How to migrate
****************

Swap the import and the call:

.. code-block:: python

    # markdownify
    from markdownify import markdownify

    markdownify(text)

    # turbohtml
    import turbohtml

    turbohtml.parse(text).to_markdown()

.. testcode::

    print(parse("<h1>Title</h1><p>Some <b>bold</b> text.</p>").to_markdown())

.. testoutput::

    # Title

    Some **bold** text.

The defaults emit opinionated GitHub-Flavored Markdown, and the markdownify options map onto the grouped
:class:`~turbohtml.Markdown` config fields with one name per concept:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `markdownify <https://github.com/matthewwithanm/python-markdownify>`__
      - turbohtml :meth:`~turbohtml.Node.to_markdown`
    - - ``heading_style`` (``atx``/``atx_closed``/``underlined``)
      - ``Markdown.Headings(style=...)`` (``"atx"``/``"atx_closed"``/``"setext"``)
    - - ``bullets``
      - ``Markdown.Lists(bullets=...)``
    - - ``strong_em_symbol``
      - ``Markdown.Inline(strong=..., emphasis=...)`` (independent, so a superset)
    - - ``sub_symbol``, ``sup_symbol``
      - ``Markdown.Inline(sub=..., sup=...)``
    - - ``escape_asterisks``, ``escape_underscores``
      - ``Markdown.Escaping(asterisks=..., underscores=...)``
    - - ``escape_misc``
      - ``Markdown.Escaping(mode="all")``
    - - ``autolinks``
      - ``Markdown.Links(autolink=...)``
    - - ``default_title``
      - ``Markdown.Links(title=...)``
    - - ``table_infer_header``
      - ``Markdown.Tables(header="first")`` (the default) vs ``"none"``
    - - ``newline_style`` (``spaces``/``backslash``)
      - ``Markdown.Document(line_break=...)`` (``"spaces"``/``"backslash"``)
    - - ``strip_document``
      - ``Markdown.Document(trim=...)`` (``"strip"``/``"lstrip"``/``"rstrip"``/``"none"``)
    - - ``wrap``, ``wrap_width``
      - ``Markdown.Wrapping(width=...)`` (``0`` leaves lines unwrapped)
    - - ``code_language``
      - ``Markdown.Code(language=...)``
    - - ``strip``, ``convert``
      - ``Markdown(strip=...)``, ``Markdown(convert=...)`` (mutually exclusive tag filters)
    - - ``convert_<tag>`` overrides
      - ``Markdown(converters=...)`` argument

Subclassing ``MarkdownConverter`` to override ``convert_<tag>`` becomes a mapping of tag name to a ``callable(element,
content) -> str``. Each callable receives the :class:`~turbohtml.Element` and its already-rendered child Markdown and
returns the replacement text:

.. code-block:: python

    from turbohtml import Markdown


    def convert_a(element, content):
        return f"[{content}]({element.attrs.get('href', '')})"


    turbohtml.parse(text).to_markdown(Markdown(converters={"a": convert_a}))

**********************
 Gotchas and pitfalls
**********************

- The bold and italic markers are independent (``Markdown.Inline(strong=...)`` and ``Markdown.Inline(emphasis=...)``),
  where markdownify derives both from one ``strong_em_symbol``; set both to reproduce its behavior (``strong_em_symbol``
  ``"_"`` becomes ``Markdown.Inline(strong="__", emphasis="_")``).
- :meth:`~turbohtml.Node.to_markdown` is a method on any node, so convert a subtree by calling it on the element you
  selected (``doc.find("article").to_markdown()``) instead of slicing the HTML string first.
- markdownify's parser-selection options (``bs4_options``) are dropped, since turbohtml always runs the WHATWG
  algorithm; malformed markup is repaired to the same tree a browser would build.
- ``Markdown.Links(base_url=...)`` does simple prefixing rather than full RFC-3986 URL resolution.
- markdownify's ``strip_document`` defaults to ``STRIP``; turbohtml's ``Markdown.Document(trim=...)`` also defaults to
  ``"strip"``, so document-edge trimming matches by default.
