##############
 From pyquery
##############

.. package-meta:: pyquery gawel/pyquery

`pyquery <https://github.com/gawel/pyquery>`_ puts a jQuery-style fluent, chainable wrapper over `lxml
<https://lxml.de>`_/`cssselect <https://github.com/scrapy/cssselect>`_, so you select and mutate a document with method
chains. A single ``PyQuery`` object holds a matched set of nodes; calling it with a CSS selector, chaining ``.filter``,
``.find``, ``.eq``, or ``.closest``, and reading or writing attributes, text, HTML, and classes all return either a new
wrapper or a scalar. It is a common pick for scraping and templating code that wants the DOM-manipulation feel of jQuery
without a browser.

turbohtml covers that ground with :class:`turbohtml.query.Query`, a fully type-annotated fluent wrapper whose selector,
attribute, and class primitives run in the C extension over a native tree, so the same chaining idiom ports across with
almost no rename and a large speed margin.

**********************
 turbohtml vs pyquery
**********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - pyquery
    - - Scope
      - WHATWG HTML parser plus a jQuery-style ``Query`` wrapper over a native tree
      - jQuery-style wrapper only; parsing and the tree come from lxml
    - - Feature breadth
      - CSS select/filter/traverse, attr/text/html/class ops, node-level mutation and XPath 1.0
      - CSS and XPath on the wrapper itself, broad jQuery method set, a network-fetching constructor
    - - Performance
      - Selector and attribute primitives in C over a native tree; several to ~100x faster on the shared surface
      - Python wrapper delegating to lxml/cssselect
    - - Typing
      - Fully type annotated with shipped stubs
      - No bundled type hints
    - - Dependencies
      - Self-contained C extension, no Python runtime deps
      - Depends on lxml and cssselect
    - - Maintenance
      - Actively developed alongside the parser
      - Mature, lightly maintained wrapper

Feature overlap
===============

The shared surface ports one-to-one from a matched set built by calling the wrapper with a selector:

- Construction and selection: ``PyQuery(html)`` -> :class:`Query(parse(html)) <turbohtml.query.Query>`, then
  ``query("div.foo")`` and ``query("a").find("span")``.
- Set narrowing and traversal: :meth:`~turbohtml.query.Query.filter`, :meth:`~turbohtml.query.Query.eq`,
  :meth:`~turbohtml.query.Query.closest`, :meth:`~turbohtml.query.Query.parent`,
  :meth:`~turbohtml.query.Query.children`, :meth:`~turbohtml.query.Query.siblings` keep the same names.
- Reads and attribute writes: :meth:`~turbohtml.query.Query.attr`, :meth:`~turbohtml.query.Query.text`,
  :meth:`~turbohtml.query.Query.html` keep the same names.
- Class ops: :meth:`~turbohtml.Element.add_class`, :meth:`~turbohtml.Element.remove_class`,
  :meth:`~turbohtml.Element.toggle_class`, :meth:`~turbohtml.Element.has_class`, available on both
  :class:`~turbohtml.query.Query` and :class:`~turbohtml.Element`.
- Iteration: ``for item in pq("a").items()`` -> :meth:`for item in query("a").items() <turbohtml.query.Query.items>`.

What turbohtml adds
===================

- A full WHATWG HTML parser in the same package, so parsing and querying share one native tree instead of delegating to
  lxml.
- C-resident selector and attribute primitives, giving the large speed margin measured below.
- Shipped type stubs for the whole surface, including ``Query`` and the node API.
- No third-party runtime dependencies: the tree, selectors, and mutation all live in one self-contained C extension.

What pyquery has that turbohtml does not
========================================

- **A network-fetching constructor.** ``PyQuery(url=...)`` fetches over HTTP for you. turbohtml has no equivalent; fetch
  with `httpx <https://www.python-httpx.org>`_ (or any client) and hand the bytes to :func:`turbohtml.parse`.
- **XPath on the fluent wrapper.** pyquery exposes lxml's ``.xpath(...)`` directly on a matched set. turbohtml's
  ``Query`` is CSS-only; drop to the node-level :meth:`~turbohtml.Node.xpath` (XPath 1.0) or the
  :meth:`~turbohtml.Node.find` grammar via :meth:`Query.items <turbohtml.query.Query.items>`.
- **``.wrap_all`` over a non-contiguous set.** pyquery wraps any matched set in one container. turbohtml's node API
  covers the tree-clean shapes (see below) but has no counterpart for an arbitrary scattered set;
  :meth:`~turbohtml.Element.append` the nodes into a new element and place it yourself.

Performance
===========

.. bench-table::
    :file: bench/pyquery.json

The whole shared surface -- chaining a select/filter/read, setting content, bulk-editing tags, and reading a value off
every match -- runs several to a hundred times faster because the wrapper edits its native tree in C and skips a
redundant de-duplication when a chain starts from a single node, where pyquery drives lxml under its jQuery-style
wrapper.

****************
 How to migrate
****************

Build a :class:`~turbohtml.query.Query` from a parsed document and call it with a selector; the method chains port
almost name for name:

.. testcode::

    from turbohtml import parse
    from turbohtml.query import Query

    query = Query(parse("<div><a href='/u'>l</a><a>m</a></div>"))
    print(query("a").filter("[href]").eq(0).add_class("seen").attr("href"))
    print([anchor.text() for anchor in query("a").items()])

.. testoutput::

    /u
    ['l', 'm']

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `pyquery <https://pyquery.readthedocs.io/>`__
      - turbohtml
    - - ``pq = PyQuery(html)``
      - :class:`Query(parse(html)) <turbohtml.query.Query>`
    - - ``pq("div.foo")``, ``pq("a").find("span")``
      - ``query("div.foo")``, ``query("a").find("span")``
    - - ``.filter(sel)``, ``.eq(i)``, ``.closest(sel)``
      - the same names
    - - ``.attr("href")``, ``.attr("k", "v")``
      - the same names
    - - ``.text()``, ``.html()``
      - the same names
    - - ``.add_class(c)``, ``.remove_class(c)``, ``.toggle_class(c)``, ``.has_class(c)``
      - :meth:`~turbohtml.Element.add_class`, :meth:`~turbohtml.Element.remove_class`,
        :meth:`~turbohtml.Element.toggle_class`, :meth:`~turbohtml.Element.has_class` (also on
        :class:`~turbohtml.query.Query`)
    - - ``.parent()``, ``.children()``, ``.siblings()``
      - the same names
    - - iterating ``for item in pq("a").items()``
      - :meth:`for item in query("a").items() <turbohtml.query.Query.items>`
    - - jQuery ``pq("script").remove()``, ``pq(".box b").remove()``
      - :meth:`node.remove("script") <turbohtml.Node.remove>`, :meth:`node.remove(".box b") <turbohtml.Node.remove>`
    - - jQuery ``$(".box b").contents().unwrap()`` (drop the tag, keep the text)
      - :meth:`node.strip_tags(".box b") <turbohtml.Node.strip_tags>`

pyquery's ``.wrap_all(html)`` wraps a whole matched set in one new container in place; the node API has two methods for
the shapes that fit a tree model cleanly. :meth:`~turbohtml.Element.wrap_children` boxes every child of a container, and
:meth:`~turbohtml.Node.wrap_siblings` wraps a node and the contiguous run of siblings after it (through an ``until``
node, or to the last sibling), so ``query("p").wrap_all("<div/>")`` over a run of adjacent paragraphs becomes
``first.wrap_siblings(Element("div"), until=last)``:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `pyquery <https://pyquery.readthedocs.io/>`__
      - turbohtml
    - - ``pq("section").contents().wrap_all("<div/>")``
      - :meth:`section.wrap_children(Element("div")) <turbohtml.Element.wrap_children>`
    - - ``pq(run).wrap_all("<div/>")`` over a contiguous run
      - :meth:`first.wrap_siblings(Element("div"), until=last) <turbohtml.Node.wrap_siblings>`

pyquery's content setters -- ``.html(markup)`` reparses a matched element's children and ``.text(s)`` replaces them with
one verbatim text node -- map onto three element methods. :meth:`~turbohtml.Element.set_inner_html` parses the markup as
a fragment in the element's context and replaces its children; :meth:`~turbohtml.Element.set_text` replaces them with
one verbatim text node; and :meth:`~turbohtml.Element.insert_adjacent_html` splices a parsed fragment at a DOM position
(the ``.append(markup)`` / ``insertAdjacentHTML`` shape):

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `pyquery <https://pyquery.readthedocs.io/>`__
      - turbohtml
    - - ``pq(el).html(markup)``
      - :meth:`el.set_inner_html(markup) <turbohtml.Element.set_inner_html>`
    - - ``pq(el).text(s)``
      - :meth:`el.set_text(s) <turbohtml.Element.set_text>`
    - - ``pq(el).append(markup)``
      - :meth:`el.insert_adjacent_html("beforeend", markup) <turbohtml.Element.insert_adjacent_html>`

**********************
 Gotchas and pitfalls
**********************

- ``.wrap_all`` over an **arbitrary, non-contiguous** set of nodes has no single node-method counterpart (the set has no
  shared anchor to place the wrapper at); wrap the contiguous run, or :meth:`~turbohtml.Element.append` the scattered
  nodes into one new element and place it yourself.
- pyquery's **network-fetching constructor** (``PyQuery(url=...)``) is out of scope: fetch with `httpx
  <https://www.python-httpx.org>`_ (or any client) and hand the bytes to :func:`turbohtml.parse`.
- pyquery exposes lxml's ``.xpath(...)`` on the fluent wrapper itself; turbohtml's ``Query`` is CSS-only, so an XPath
  chain drops to the node-level :meth:`~turbohtml.Node.xpath` (XPath 1.0) or the :meth:`~turbohtml.Node.find` grammar
  via :meth:`Query.items <turbohtml.query.Query.items>`.
- turbohtml parses to the **WHATWG spec**, so a malformed document is fixed up exactly as a browser would (implied
  ``<tbody>``, reparented ``<head>`` content); pyquery's tree follows lxml's HTML parser, which can differ on the same
  broken input.
