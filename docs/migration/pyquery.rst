##############
 From pyquery
##############

`pyquery <https://github.com/gawel/pyquery>`_ puts a jQuery-style fluent wrapper over `lxml
<https://lxml.de>`_/`cssselect <https://github.com/scrapy/cssselect>`_. turbohtml ships the same chaining idiom as
:class:`turbohtml.query.Query`, so the method chains port almost name for name; build one from a parsed document and
call it with a selector:

.. testcode::

    from turbohtml import parse
    from turbohtml.query import Query

    query = Query(parse("<div><a href='/u'>l</a><a>m</a></div>"))
    print(query("a").filter("[href]").eq(0).add_class("seen").attr("href"))
    print([anchor.text() for anchor in query("a").items()])

.. testoutput::

    /u
    ['l', 'm']

The idiom translates directly:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - pyquery
      - turbohtml
    - - ``pq = PyQuery(html)``
      - ``query = Query(parse(html))``
    - - ``pq("div.foo")``, ``pq("a").find("span")``
      - ``query("div.foo")``, ``query("a").find("span")``
    - - ``.filter(sel)``, ``.eq(i)``, ``.closest(sel)``
      - the same names
    - - ``.attr("href")``, ``.attr("k", "v")``
      - ``.attr("href")``, ``.attr("k", "v")``
    - - ``.text()``, ``.html()``
      - ``.text()``, ``.html()``
    - - ``.add_class(c)``, ``.remove_class(c)``, ``.has_class(c)``
      - the same names
    - - ``.parent()``, ``.children()``, ``.siblings()``
      - the same names
    - - iterating ``for item in pq("a").items()``
      - ``for item in query("a").items()``

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
      - ``section.wrap_children(Element("div"))``
    - - ``pq(run).wrap_all("<div/>")`` over a contiguous run
      - ``first.wrap_siblings(Element("div"), until=last)``

Three limits are worth stating. ``.wrap_all`` over an **arbitrary, non-contiguous** set of nodes has no single
node-method counterpart (the set has no shared anchor to place the wrapper at); wrap the contiguous run, or
:meth:`~turbohtml.Element.append` the scattered nodes into one new element and place it yourself. pyquery's
**network-fetching constructor** (``PyQuery(url=...)``) is also out of scope: fetch with `httpx
<https://www.python-httpx.org>`_ (or any client) and hand the bytes to :func:`turbohtml.parse`. And pyquery exposes
lxml's ``.xpath(...)`` on the fluent wrapper itself; turbohtml's ``Query`` is CSS-only, so an XPath chain drops to the
node-level :meth:`~turbohtml.Node.xpath` (XPath 1.0) or the :meth:`~turbohtml.Node.find` grammar via :meth:`Query.items
<turbohtml.query.Query.items>`. The :doc:`/performance` page's fluent-chaining benchmark times the same chain against
pyquery.
