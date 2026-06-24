##############
 From pyquery
##############

.. image:: https://static.pepy.tech/badge/pyquery/month
    :alt: pyquery monthly downloads
    :target: https://pepy.tech/project/pyquery

`pyquery <https://github.com/gawel/pyquery>`_ puts a jQuery-style fluent, chainable wrapper over `lxml
<https://lxml.de>`_/`cssselect <https://github.com/scrapy/cssselect>`_, so you select and mutate a document with method
chains.

***************
 Why turbohtml
***************

turbohtml ships the same chaining idiom as :class:`turbohtml.query.Query`, fully type annotated, with the selector and
attribute primitives running in C. The wrapper skips a redundant de-duplication when a chain starts from one node, and
edits its native tree in C where pyquery drives lxml under its jQuery-style wrapper, so the whole surface -- chaining a
select/filter/read, setting content, bulk-editing tags, and reading a value off every match -- runs several to a hundred
times faster:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - operation
      - turbohtml
      - pyquery
    - - select, filter, read (4 kB)
      - 0.9 µs
      - 16.0 µs (17.2x)
    - - select, filter, read (9.6 kB)
      - 1.0 µs
      - 16.5 µs (16.1x)
    - - select, filter, read (92 kB)
      - 21.8 µs
      - 278 µs (12.8x)
    - - set inner HTML (9.6 kB)
      - 1.3 µs
      - 7.7 µs (5.9x)
    - - set text (9.6 kB)
      - 0.13 µs
      - 4.6 µs (35.4x)
    - - drop subtree (92 kB)
      - 554 µs
      - 2.06 ms (3.7x)
    - - keep content, unwrap (92 kB)
      - 607 µs
      - 2.35 ms (3.9x)
    - - ``@href`` per match (9.6 kB)
      - 0.1 µs
      - 4.8 µs (96.6x)
    - - ``@href`` per match (92 kB)
      - 8.2 µs
      - 542 µs (65.8x)
    - - text per match (92 kB)
      - 8.0 µs
      - 297 µs (37.0x)

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

    - - pyquery
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

    - - pyquery
      - turbohtml
    - - ``pq(el).html(markup)``
      - :meth:`el.set_inner_html(markup) <turbohtml.Element.set_inner_html>`
    - - ``pq(el).text(s)``
      - :meth:`el.set_text(s) <turbohtml.Element.set_text>`
    - - ``pq(el).append(markup)``
      - :meth:`el.insert_adjacent_html("beforeend", markup) <turbohtml.Element.insert_adjacent_html>`

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
