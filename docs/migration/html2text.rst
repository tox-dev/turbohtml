################
 From html2text
################

.. package-meta:: html2text Alir3z4/html2text

`html2text <https://github.com/Alir3z4/html2text>`_ converts HTML to Markdown. It subclasses the standard library
``HTMLParser`` and streams tokens through a large, well-worn option surface: list markers, emphasis and strong markers,
link styles (inline or reference), image handling, table rendering, line wrapping at ``body_width``, Unicode folding via
its ``UNIFIABLE`` table, and a ``google_doc`` mode that reads the inline CSS a Google Docs HTML export carries. It ships
a command-line converter alongside the library and is a long-standing default for turning arbitrary web HTML into
readable Markdown in scrapers, mail-to-text pipelines, and documentation tooling.

turbohtml covers the same conversion through :meth:`~turbohtml.Node.to_markdown`, a single fully typed method on the
parsed WHATWG tree. It walks the tree in C instead of driving a Python ``HTMLParser``, and groups the flat html2text
options into named config dataclasses so each concept has one name.

************************
 turbohtml vs html2text
************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - html2text
    - - Scope
      - Full WHATWG HTML parser and tree with Markdown, plain-text, and HTML serialization
      - HTML-to-Markdown conversion only
    - - Feature breadth
      - Same Markdown option surface (lists, inline marks, links, images, tables, wrapping, code, Google Docs mode) plus
        text extraction, selectors, and sanitization on the shared tree
      - Broad, mature Markdown-conversion options including ``google_doc`` and CLI flags
    - - Performance
      - C tree walk, roughly 50x faster on the benchmark below
      - Pure-Python ``HTMLParser`` subclass
    - - Typing
      - Fully type annotated, config via frozen dataclasses
      - Untyped public API
    - - Dependencies
      - Compiled C extension (prebuilt wheels)
      - Pure Python, no compiled dependency
    - - Maintenance
      - Actively developed
      - Long-standing, widely deployed, stable

Feature overlap
===============

Port these html2text calls 1:1 onto :meth:`~turbohtml.Node.to_markdown` with a :class:`~turbohtml.Markdown` config:

- ``html2text.html2text(text)`` -> ``turbohtml.parse(text).to_markdown()``.
- List markers, emphasis/strong markers, and quote characters (``ul_item_mark``, ``emphasis_mark``, ``strong_mark``,
  ``open_quote``, ``close_quote``).
- Link handling: ignore, skip-internal, inline vs reference style, and ``baseurl`` prefixing.
- Image handling: markdown, alt-only, ignore, or raw HTML, plus a default alt string.
- Table rendering: markdown, strip, or raw HTML, with column padding.
- Line wrapping at a width, for list items and links.
- Unicode folding (``unicode_snob``), code marking and block style, single-line-break spacing, aggressive escaping.
- The ``google_doc`` inline-CSS mode and its list-indent step.

What turbohtml adds
===================

- One parsed tree serves Markdown, layout-aware plain text (:meth:`~turbohtml.Node.to_text`), and HTML
  (:meth:`~turbohtml.Node.serialize`); html2text is Markdown-only.
- Convert any subtree by calling :meth:`~turbohtml.Node.to_markdown` on a selected element
  (``doc.find("article").to_markdown()``) instead of pre-slicing the HTML string.
- Full spec-compliant WHATWG parsing feeds the conversion, so malformed markup is repaired the same way a browser does
  before it is rendered to Markdown.
- Grouped, frozen-dataclass config (``Markdown.Links``, ``Markdown.Images``, ``Markdown.Tables`` ...) is fully typed and
  discoverable, versus html2text's flat set of untyped instance attributes.

What html2text has that turbohtml does not
==========================================

- A command-line converter and ``python -m html2text`` entry point. turbohtml exposes Markdown as a library method only;
  wrap ``turbohtml.parse(open(path).read()).to_markdown()`` in a short script for equivalent shell use.
- Pure-Python install with no compiled extension. html2text runs anywhere a Python interpreter does; turbohtml needs a
  prebuilt wheel (or a C toolchain to build from source) for the target platform.
- ``baseurl`` in html2text and ``Markdown.Links(base_url=...)`` both prefix rather than fully resolve; neither does
  RFC-3986 resolution, so there is no gap here, but do not expect turbohtml to add it.

Performance
===========

.. bench-table::
    :file: bench/html2text.json

****************
 How to migrate
****************

Swap the import and the call:

.. code-block:: python

    # html2text
    import html2text

    html2text.html2text(text)

    # turbohtml
    import turbohtml

    turbohtml.parse(text).to_markdown()

.. testcode::

    print(parse("<h1>Title</h1><p>Some <b>bold</b> text.</p>").to_markdown())

.. testoutput::

    # Title

    Some **bold** text.

The html2text options map onto the grouped :class:`~turbohtml.Markdown` config fields with one name per concept:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `html2text <https://github.com/Alir3z4/html2text>`__
      - turbohtml :meth:`~turbohtml.Node.to_markdown`
    - - ``ul_item_mark``
      - ``Markdown.Lists(bullets=...)``
    - - ``emphasis_mark``, ``strong_mark``
      - ``Markdown.Inline(emphasis=..., strong=...)``
    - - ``ignore_emphasis``
      - ``Markdown.Inline(ignore_emphasis=...)``
    - - ``ignore_links``
      - ``Markdown.Links(ignore=...)``
    - - ``skip_internal_links``
      - ``Markdown.Links(skip_internal=...)``
    - - ``inline_links``
      - ``Markdown.Links(style=...)`` (``"inline"``/``"reference"``)
    - - ``ignore_images``, ``images_to_alt``, ``images_as_html``, ``images_with_size``
      - ``Markdown.Images(mode=...)`` (``"markdown"``/``"alt"``/``"ignore"``/``"html"``)
    - - ``default_image_alt``
      - ``Markdown.Images(default_alt=...)``
    - - ``ignore_tables``, ``bypass_tables``
      - ``Markdown.Tables(mode=...)`` (``"markdown"``/``"strip"``/``"html"``)
    - - ``pad_tables``
      - ``Markdown.Tables(pad=...)``
    - - ``body_width``, ``wrap_list_items``, ``wrap_links``
      - ``Markdown.Wrapping(width=..., list_items=..., links=...)``
    - - ``unicode_snob`` (and the ``UNIFIABLE`` table)
      - ``Markdown.Document(transliterate=...)``
    - - ``mark_code``
      - ``Markdown.Code(mark=...)``
    - - ``backquote_code_style``
      - ``Markdown.Code(block_style=...)`` (``"fenced"``/``"indented"``)
    - - ``single_line_break``
      - ``Markdown.Document(block_spacing="single")``
    - - ``baseurl``
      - ``Markdown.Links(base_url=...)``
    - - ``open_quote``, ``close_quote``
      - ``Markdown.Inline(quote_open=..., quote_close=...)``
    - - ``escape_snob``
      - ``Markdown.Escaping(mode="all")``
    - - ``google_doc``
      - ``Markdown.GoogleDoc(enabled=...)`` (or the ``Markdown.google_doc()`` preset)
    - - ``google_list_indent``
      - ``Markdown.GoogleDoc(list_indent=...)``
    - - ``hide_strikethrough``
      - ``Markdown.Inline(hide_strikethrough=...)``

``Markdown.google_doc()`` reads the inline-CSS styling a Google Docs HTML export carries: a ``font-weight`` of ``bold``
or ``700``--``900`` becomes ``strong``, ``font-style:italic`` becomes ``emphasis``, a ``Courier New``/``Consolas``
``font-family`` becomes an inline code span, ``list-style-type`` picks the list marker, and each
``Markdown.GoogleDoc(list_indent=...)`` pixels of ``margin-left`` add one list-nesting level. With
``Markdown.Inline(hide_strikethrough=True)`` a ``text-decoration:line-through`` drops the struck text.

.. testcode::

    from turbohtml import Markdown

    export = '<p><span style="font-weight:700">Quarterly</span> revenue</p>'
    print(parse(export).to_markdown(Markdown.google_doc()))

.. testoutput::

    **Quarterly** revenue

**********************
 Gotchas and pitfalls
**********************

- :meth:`~turbohtml.Node.to_markdown` is a method on any node, so convert a subtree by calling it on the element you
  selected (``doc.find("article").to_markdown()``) instead of slicing the HTML string first.
- Layout-aware plain text (the ``inscriptis`` role, :meth:`~turbohtml.Node.to_text`) is a separate method; for the
  unstructured concatenation read :attr:`~turbohtml.Node.text`.
- ``base_url`` does simple prefixing rather than full RFC-3986 URL resolution, matching html2text's ``baseurl``.
- html2text wraps prose at ``body_width`` (78) by default; turbohtml wraps only when you set
  ``Markdown.Wrapping(width=...)``. Set it explicitly to reproduce wrapped output, or leave it unset for unwrapped
  Markdown.
