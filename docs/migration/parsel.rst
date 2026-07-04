#############
 From parsel
#############

.. package-meta:: parsel scrapy/parsel

`parsel <https://parsel.readthedocs.io>`_ is `Scrapy <https://scrapy.org>`_'s extraction-oriented selector library. A
:class:`~parsel.selector.Selector` wraps a document and every query returns a :class:`~parsel.selector.SelectorList`;
you pull *strings* out of it with :meth:`~parsel.selector.SelectorList.get` /
:meth:`~parsel.selector.SelectorList.getall`, reaching text and attribute values through the non-standard ``::text`` and
``::attr(name)`` pseudo-elements. It layers cssselect (CSS-to-XPath translation), lxml/libxml2 (parsing and evaluation),
w3lib, and jmespath into one façade, so a single object exposes CSS, XPath, JMESPath over embedded JSON, and regex
extraction. It is the workhorse behind Scrapy spiders and is widely used standalone for scraping and data extraction.

turbohtml covers the same extraction ground with a native, spec-compliant HTML5 parser: :meth:`~turbohtml.Node.select`,
:meth:`~turbohtml.Node.xpath`, :meth:`~turbohtml.Node.re`, and attribute access return typed :class:`~turbohtml.Node`
objects, and the string-shaping helpers (``.text``, ``.html``, :meth:`~turbohtml.Element.attr`) are ordinary members
rather than pseudo-element syntax.

*********************
 turbohtml vs parsel
*********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - parsel
    - - Scope
      - Spec-compliant HTML5 parser plus native CSS/XPath selection, regex extraction, mutation, and serialization
      - Extraction-only façade over lxml, cssselect, w3lib, and jmespath; no tree-construction of its own
    - - Feature breadth
      - CSS Selectors Level 4, XPath, regex, JMESPath (via stdlib), markdown/text/minify/sanitize output, DOM mutation
      - CSS (translated to XPath), XPath, regex, JMESPath over JSON, XML/RSS parsing mode with namespaces
    - - Performance
      - Compiles a selector once and matches by interned integer atoms; see the table below
      - Re-translates each ``.css()`` to XPath and re-evaluates on libxml2 per call
    - - Typing
      - Fully typed, ships ``.pyi`` stubs; queries return typed :class:`~turbohtml.Node`
      - Returns :class:`~parsel.selector.SelectorList` of strings; typed but string-centric
    - - Dependencies
      - Self-contained C extension, no runtime dependencies
      - Requires lxml, cssselect, w3lib, and jmespath
    - - Maintenance
      - Actively developed
      - Actively maintained under the Scrapy project

Feature overlap
===============

These map 1:1 and port with a rename:

- CSS selection: :meth:`~parsel.selector.Selector.css` becomes :meth:`~turbohtml.Node.select` /
  :meth:`~turbohtml.Node.select_one`.
- XPath: :meth:`~parsel.selector.Selector.xpath` becomes :meth:`~turbohtml.Node.xpath`,
  :meth:`~turbohtml.Node.xpath_one`, or :meth:`~turbohtml.Node.xpath_iter`, including the ``namespaces=`` keyword.
- Regex extraction: :meth:`~parsel.selector.Selector.re` / :meth:`~parsel.selector.Selector.re_first` become
  :meth:`~turbohtml.Node.re` / :meth:`~turbohtml.Node.re_first`, and the ``attr`` keyword runs the pattern over an
  attribute value instead of the text.
- Attribute access: :attr:`~parsel.selector.Selector.attrib` becomes :attr:`~turbohtml.Element.attrs`, and
  ``::attr(name)`` becomes :meth:`~turbohtml.Element.attr`.
- Text extraction: ``::text`` becomes :attr:`~turbohtml.Node.text`.
- The underlying element: :attr:`sel.root <parsel.selector.Selector.root>` (an lxml element) becomes the
  :class:`~turbohtml.Node` itself.

What turbohtml adds
===================

- A WHATWG-conformant HTML5 tree builder. parsel parses through libxml2's HTML parser, which does not follow the spec's
  tree-construction algorithm; turbohtml matches browser parsing on malformed markup.
- CSS Selectors Level 4 evaluated by a native engine, not translated to XPath and handed to libxml2.
- Tree mutation on the same object: :meth:`~turbohtml.Node.prune`, :meth:`~turbohtml.Node.remove`,
  :meth:`~turbohtml.Node.strip_tags`, :meth:`~turbohtml.Element.set_text`,
  :meth:`~turbohtml.Element.insert_adjacent_html`.
- Output conversions built in: :meth:`~turbohtml.Node.serialize`, :meth:`~turbohtml.Node.to_markdown`,
  :meth:`~turbohtml.Node.to_text`.
- :meth:`~turbohtml.Node.matches` and :meth:`~turbohtml.Node.closest` for testing and walking without a fresh query.
- No third-party runtime dependencies to install or pin.

What parsel has that turbohtml does not
=======================================

- JMESPath / JSON selectors (:meth:`~parsel.selector.Selector.jmespath`): no equivalent. Parse the JSON payload with
  :mod:`json` and query it with the ``jmespath`` package yourself.
- An XML parsing mode (``Selector(type="xml")``) for RSS/Atom and other XML feeds, with namespace registration: no
  equivalent, since turbohtml is an HTML parser. turbohtml's :meth:`~turbohtml.Node.xpath` accepts a ``namespaces=``
  keyword for HTML documents, but it does not parse standalone XML.
- The ``::text`` / ``::attr()`` pseudo-elements themselves: not CSS-standard and not parsed by turbohtml. Read
  :attr:`~turbohtml.Node.text` and :meth:`~turbohtml.Element.attr` off the selected node instead.

Performance
===========

turbohtml compiles a selector against the tree once and matches by comparing interned integer atoms, where parsel
translates every ``.css()`` to XPath with cssselect and re-evaluates it on libxml2 per call, so a reused query -- and
pulling the values out of every match, parsel's whole point -- runs tens to hundreds of times faster:

.. bench-table::
    :file: bench/parsel.json

****************
 How to migrate
****************

Replace ``Selector(text=html)`` with :func:`turbohtml.parse`, then swap the string-extraction calls for typed-node
access. The regex helpers :meth:`~turbohtml.Node.re` and :meth:`~turbohtml.Node.re_first` carry over directly, including
their ``attr`` keyword.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `parsel <https://parsel.readthedocs.io/>`__
      - turbohtml
    - - :class:`Selector(text=html) <parsel.selector.Selector>`
      - :func:`turbohtml.parse`
    - - :meth:`parsel.selector.Selector.css`, :meth:`parsel.selector.Selector.xpath`
      - :meth:`~turbohtml.Node.select`, :meth:`~turbohtml.Node.xpath`
    - - ``sel.css("a").get()`` (outer HTML)
      - :meth:`~turbohtml.Node.select_one` then :attr:`~turbohtml.Node.html`
    - - ``sel.css("a::text").get()``, ``.getall()``
      - :attr:`~turbohtml.Node.text` off each selected node
    - - ``sel.css("a::attr(href)").get()``, ``.getall()``
      - :meth:`~turbohtml.Element.attr` off each selected node
    - - ``sel.xpath("//a/@href").getall()``
      - :meth:`~turbohtml.Node.xpath` (already yields the values)
    - - :attr:`parsel.selector.Selector.attrib`
      - :attr:`~turbohtml.Element.attrs`
    - - :meth:`parsel.selector.Selector.re`, :meth:`parsel.selector.Selector.re_first`
      - :meth:`~turbohtml.Node.re`, :meth:`~turbohtml.Node.re_first`
    - - ``sel.css("a::attr(href)").re(pattern)``
      - :meth:`~turbohtml.Node.re` with ``attr="href"``
    - - :attr:`sel.root <parsel.selector.Selector.root>` (an lxml element)
      - the :class:`~turbohtml.Node` itself

.. testcode::

    doc = parse('<a href="/x">home</a><a href="/y">about</a>')
    print([a.attr("href") for a in doc.select("a")])
    print(doc.select_one("a").text)
    print(doc.xpath("//a/@href"))
    print([a.re_first(r"\w+") for a in doc.select("a")])
    print(doc.select_one("a").re_first(r"/(\w+)", attr="href"))

.. testoutput::

    ['/x', '/y']
    home
    ['/x', '/y']
    ['home', 'about']
    x

**********************
 Gotchas and pitfalls
**********************

- parsel's ``::text`` and ``::attr()`` pseudo-elements are not CSS standard and turbohtml does not parse them; read
  :attr:`~turbohtml.Node.text` and :meth:`~turbohtml.Element.attr` off the selected node instead.
- :meth:`~parsel.selector.SelectorList.get` / :meth:`~parsel.selector.SelectorList.getall` return strings; turbohtml
  returns nodes, so choose ``.text``, ``.html``, :meth:`~turbohtml.Element.attr`, or :meth:`~turbohtml.Node.re`
  explicitly per call. A turbohtml ``xpath("//a/@href")`` already yields the attribute *values* as strings, so there is
  no ``.getall()`` to chain.
- :meth:`~turbohtml.Node.re` and :meth:`~turbohtml.Node.re_first` run over one node at a time rather than a whole
  ``SelectorList``; map them across :meth:`~turbohtml.Node.select` to cover every match.
- parsel's JSON/JMESPath selectors (:meth:`~parsel.selector.Selector.jmespath`) are not ported; run
  :mod:`json`/``jmespath`` over parsed JSON yourself.
- parsel returns ``None`` (via ``.get(default=None)``) or an empty ``SelectorList`` for a miss; turbohtml's
  :meth:`~turbohtml.Node.select_one` returns ``None`` and :meth:`~turbohtml.Node.select` returns an empty list, so guard
  the ``None`` before reading ``.text`` or :meth:`~turbohtml.Element.attr`.
- parsel drives libxml2's non-spec HTML parser, so tree shape on malformed markup can differ from turbohtml's
  WHATWG-conformant construction; re-check selectors that relied on libxml2's tolerant fixups.
