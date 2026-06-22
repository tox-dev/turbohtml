##############
 From pyquery
##############

.. image:: https://static.pepy.tech/badge/pyquery
    :alt: pyquery downloads
    :target: https://pepy.tech/project/pyquery

`pyquery <https://github.com/gawel/pyquery>`_ puts a jQuery-style fluent, chainable wrapper over `lxml
<https://lxml.de>`_/`cssselect <https://github.com/scrapy/cssselect>`_, so you select and mutate a document with method
chains.

***************
 Why turbohtml
***************

turbohtml ships the same chaining idiom as :class:`turbohtml.query.Query`, fully type annotated, with the selector and
attribute primitives running in C. The wrapper also skips a redundant de-duplication when a chain starts from one node,
so the same chain runs roughly ten times faster than pyquery's lxml-backed wrapper:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - select, filter, tag, read
      - turbohtml
      - pyquery
      - speed-up
    - - wpt page (4 kB)
      - 0.9 µs
      - 16.0 µs
      - 17.2x
    - - wpt page (9.6 kB)
      - 1.0 µs
      - 16.5 µs
      - 16.1x
    - - wpt page (92 kB)
      - 21.8 µs
      - 278 µs
      - 12.8x

*************
 The renames
*************

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

    - - pyquery
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
    - - ``.add_class(c)``, ``.remove_class(c)``, ``.has_class(c)``
      - the same names
    - - ``.parent()``, ``.children()``, ``.siblings()``
      - the same names
    - - iterating ``for item in pq("a").items()``
      - :meth:`for item in query("a").items() <turbohtml.query.Query.items>`

pyquery's ``.wrap_all(html)`` wraps a whole matched set in one new container in place; the node API has two methods for
the shapes that fit a tree model cleanly. :meth:`~turbohtml.Element.wrap_children` boxes every child of a container, and
:meth:`~turbohtml.Node.wrap_siblings` wraps a node and the contiguous run of siblings after it (through an ``until``
node, or to the last sibling), so ``query("p").wrap_all("<div/>")`` over a run of adjacent paragraphs becomes
``first.wrap_siblings(Element("div"), until=last)``:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - pyquery
      - turbohtml
    - - ``pq("section").contents().wrap_all("<div/>")``
      - :meth:`section.wrap_children(Element("div")) <turbohtml.Element.wrap_children>`
    - - ``pq(run).wrap_all("<div/>")`` over a contiguous run
      - :meth:`first.wrap_siblings(Element("div"), until=last) <turbohtml.Node.wrap_siblings>`

**********
 Pitfalls
**********

- ``.wrap_all`` over an **arbitrary, non-contiguous** set of nodes has no single node-method counterpart (the set has no
  shared anchor to place the wrapper at); wrap the contiguous run, or :meth:`~turbohtml.Element.append` the scattered
  nodes into one new element and place it yourself.
- pyquery's **network-fetching constructor** (``PyQuery(url=...)``) is out of scope: fetch with `httpx
  <https://www.python-httpx.org>`_ (or any client) and hand the bytes to :func:`turbohtml.parse`.
- pyquery exposes lxml's ``.xpath(...)`` on the fluent wrapper itself; turbohtml's ``Query`` is CSS-only, so an XPath
  chain drops to the node-level :meth:`~turbohtml.Node.xpath` (XPath 1.0) or the :meth:`~turbohtml.Node.find` grammar
  via :meth:`Query.items <turbohtml.query.Query.items>`.
