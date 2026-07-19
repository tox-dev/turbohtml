#################
 From selectolax
#################

.. package-meta:: selectolax rushter/selectolax

`selectolax <https://github.com/rushter/selectolax>`_ is a fast HTML parser that wraps a C engine and exposes CSS
selection. It ships two backends: ``HTMLParser`` over Modest and ``LexborHTMLParser`` over `lexbor
<https://lexbor.com>`_. You query with CSS selectors (``css`` / ``css_first``), read text through the ``text()``
*method*, reach attributes via ``node.attributes``, and mutate the tree with a small set of operations (``decompose``,
``unwrap``, ``strip_tags``, ``unwrap_tags``). It has no XPath and no regex extraction. Its niche is high-throughput web
scraping and data extraction where CSS selection over a compiled C tree is the whole job.

turbohtml covers the same ground with a single native, spec-compliant HTML5 engine: :meth:`~turbohtml.Node.select` /
:meth:`~turbohtml.Node.select_one` for CSS, :attr:`~turbohtml.Node.text` as a property, :attr:`~turbohtml.Element.attrs`
for attributes, and the same drop/unwrap operations. On top of that surface it adds the ``find`` / ``find_all`` filter
grammar, XPath, regex extraction, a full mutation surface, and markdown/text/minify output, all fully typed.

*************************
 turbohtml vs selectolax
*************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - selectolax
    - - Scope
      - Spec-compliant HTML5 parser plus native CSS/XPath selection, regex extraction, mutation, and serialization
      - CSS-only selection and light tree editing over a bundled C engine (Modest or lexbor); no XPath, no regex
    - - Feature breadth
      - CSS Selectors Level 4, the ``find``/``find_all`` filter grammar, XPath, regex, DOM mutation, and
        markdown/text/minify/sanitize output
      - CSS selection, ``text()`` extraction, attribute access, and a handful of drop/unwrap/decompose operations
    - - Performance
      - Compiles a selector once and matches by interned integer atoms; collects text in one C pass; see the table below
      - Fast CSS matching in C, but text collection and node access cross the C boundary per node
    - - Typing
      - Fully typed, ships ``.pyi`` stubs; queries return typed :class:`~turbohtml.Node`
      - Typed API surface, but node access is string-centric and text is a method call
    - - Dependencies
      - Self-contained C extension, no runtime dependencies
      - Self-contained C extension (Modest/lexbor bundled), no runtime dependencies
    - - Maintenance
      - Actively developed
      - Actively maintained

Feature overlap
===============

These map 1:1 and port with a rename:

- CSS selection: ``node.css(sel)`` / ``node.css_first(sel)`` become :meth:`~turbohtml.Node.select` /
  :meth:`~turbohtml.Node.select_one`.
- Selector test: ``node.css_matches(sel)`` becomes :meth:`~turbohtml.Node.matches`.
- Tag name: ``node.tag`` stays :attr:`~turbohtml.Element.tag`.
- Attributes: ``node.attributes`` becomes :attr:`~turbohtml.Element.attrs`, and a single value reads through
  :meth:`~turbohtml.Element.attr`.
- Outer HTML: ``node.html`` stays :attr:`~turbohtml.Node.html`.
- Node removal that keeps the children: ``node.unwrap()`` stays :meth:`~turbohtml.Node.unwrap`.
- Node removal with its subtree: ``node.decompose()`` stays :meth:`~turbohtml.Node.decompose`.
- Bulk tag stripping: ``parser.strip_tags([...])`` (drop tags *with* content) becomes :meth:`~turbohtml.Node.remove`,
  and ``node.unwrap_tags([...])`` (keep content) becomes :meth:`~turbohtml.Node.strip_tags`. Both turbohtml methods take
  a full CSS selector, not a tag-name list.

What turbohtml adds
===================

- The ``find`` / ``find_all`` filter grammar layered on top of CSS, with axes and regex or callable filters, where
  selectolax is CSS-only.
- XPath: :meth:`~turbohtml.Node.xpath`, :meth:`~turbohtml.Node.xpath_one`, :meth:`~turbohtml.Node.xpath_iter`.
- Regex extraction over text or an attribute value: :meth:`~turbohtml.Node.re`, :meth:`~turbohtml.Node.re_first`.
- Text as a property plus lazy iterators: :attr:`~turbohtml.Node.text`, :attr:`~turbohtml.Node.strings`,
  :attr:`~turbohtml.Node.stripped_strings`, collected in one C pass instead of a per-node method call.
- A full mutation surface on the same node: :meth:`~turbohtml.Node.prune`, :meth:`~turbohtml.Node.wrap`,
  :meth:`~turbohtml.Node.insert_before`, :meth:`~turbohtml.Node.insert_after`, :meth:`~turbohtml.Node.replace_with`,
  :meth:`~turbohtml.Element.set_text`, :meth:`~turbohtml.Element.insert_adjacent_html`.
- Output conversions built in: :meth:`~turbohtml.Node.serialize`, :meth:`~turbohtml.Node.to_markdown`,
  :meth:`~turbohtml.Node.to_text`.
- :meth:`~turbohtml.Node.closest` for walking upward without a fresh query.

What selectolax has that turbohtml does not
===========================================

- A choice of parser backends (``HTMLParser`` over Modest vs ``LexborHTMLParser`` over lexbor): no equivalent. turbohtml
  is a single native engine; there is one tree builder and no backend switch.
- ``text(deep=..., separator=..., strip=...)`` shaping text extraction in one call: no exact equivalent. Read
  :attr:`~turbohtml.Node.text` for the flat string, iterate :attr:`~turbohtml.Node.stripped_strings` and ``join`` with
  your own separator, or call :meth:`~turbohtml.Node.to_text` for a formatted rendering.
- The bundled engine's raw C-level node handles and lexbor-specific knobs: not exposed. turbohtml's public surface is
  the typed Python tree, not the underlying engine's C API.

Performance
===========

turbohtml's lighter native tree parses, selects, and serializes faster than selectolax's heavier object layer over
lexbor. It drops a set of tags with their subtrees faster (:meth:`~turbohtml.Node.remove` against ``strip_tags``, over a
92 kB page of 839 ``<code>``/``<a>``/``<q>`` elements), tests a compiled selector against every anchor
(:meth:`~turbohtml.Node.matches` against ``css_matches``) 35 to 45 times faster, and collects a node's visible text
(:attr:`~turbohtml.Node.text` against selectolax's ``text()`` method) seven to nine times faster, concatenating in one C
pass where selectolax crosses the lexbor boundary per node:

.. bench-table::
    :file: bench/selectolax.json

****************
 How to migrate
****************

Replace ``LexborHTMLParser(html)`` (or ``HTMLParser(html)``) with :func:`turbohtml.parse`, then swap ``css`` for
:meth:`~turbohtml.Node.select` and drop the parentheses on ``text``.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `selectolax <https://github.com/rushter/selectolax>`__
      - turbohtml
    - - ``LexborHTMLParser(html)``
      - :func:`turbohtml.parse`
    - - ``parser.root``, ``parser.body``
      - :attr:`doc.root <turbohtml.Document.root>`, :meth:`doc.find("body") <turbohtml.Node.find>`
    - - ``node.css("a")``, ``node.css_first("a")``
      - :meth:`~turbohtml.Node.select`, :meth:`~turbohtml.Node.select_one`
    - - ``node.css_matches("a")``
      - :meth:`~turbohtml.Node.matches`
    - - ``node.tag``
      - :attr:`~turbohtml.Element.tag` (same)
    - - ``node.attributes``
      - :attr:`~turbohtml.Element.attrs`, :meth:`~turbohtml.Element.attr`
    - - ``node.text()`` (a method)
      - :attr:`~turbohtml.Node.text` (a property), :attr:`~turbohtml.Node.strings`,
        :attr:`~turbohtml.Node.stripped_strings`
    - - ``node.html``, ``node.decompose()``, ``node.unwrap()``
      - :attr:`~turbohtml.Node.html`, :meth:`~turbohtml.Node.decompose`, :meth:`~turbohtml.Node.unwrap`
    - - ``parser.strip_tags(["script"])``, ``node.unwrap_tags(["b"])``
      - :meth:`node.remove("script") <turbohtml.Node.remove>`, :meth:`node.strip_tags("b") <turbohtml.Node.strip_tags>`

.. testcode::

    doc = parse("<ul><li>a</li><li>b</li></ul>")
    print([li.text for li in doc.select("li")])

.. testoutput::

    ['a', 'b']

**********************
 Gotchas and pitfalls
**********************

- ``node.text`` is a property in turbohtml; drop the parentheses. selectolax's ``text(deep=..., separator=...,
  strip=...)`` keywords have no single-call equivalent: use :attr:`~turbohtml.Node.stripped_strings` with your own
  ``join`` for a separator, or :meth:`~turbohtml.Node.to_text` for formatted output.
- The bulk tag strippers are named the other way around: selectolax's ``strip_tags`` drops the tags *with* their content
  (turbohtml's :meth:`~turbohtml.Node.remove`), while its ``unwrap_tags`` keeps the content (turbohtml's
  :meth:`~turbohtml.Node.strip_tags`). Both turbohtml methods take a full CSS selector, not a tag-name list.
- selectolax queries are CSS-only; there is no ``xpath`` or ``re`` to port. Where you would have chained several ``css``
  calls, turbohtml's ``find`` / ``find_all`` filter grammar, :meth:`~turbohtml.Node.xpath`, and
  :meth:`~turbohtml.Node.re` cover the same intent in one call.
- ``css_first`` returns ``None`` on a miss; so does :meth:`~turbohtml.Node.select_one`, and
  :meth:`~turbohtml.Node.select` returns an empty list, so guard the ``None`` before reading ``.text`` or
  :meth:`~turbohtml.Element.attr`.
- selectolax's lexbor-specific knobs, its Modest-vs-lexbor backend choice, and its raw C-level node handles are not
  exposed by turbohtml; the public surface is the typed Python tree, not the underlying engine's C API.
