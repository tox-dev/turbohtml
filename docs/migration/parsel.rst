#############
 From parsel
#############

`parsel <https://github.com/scrapy/parsel>`_ (Scrapy's selector library) is extraction-oriented: a ``Selector`` query
returns a ``SelectorList`` and you pull *strings* out of it with ``.get()`` / ``.getall()``, using the ``::text`` and
``::attr(name)`` pseudo-elements to reach text and attribute values. turbohtml instead returns :class:`~turbohtml.Node`
objects from :meth:`~turbohtml.Node.select` and :meth:`~turbohtml.Node.xpath`, and you read
:attr:`~turbohtml.Node.text`, :meth:`~turbohtml.Element.attr`, or :attr:`~turbohtml.Node.html` off each node, so the
non-standard ``::text`` / ``::attr()`` pseudo-elements become ordinary text and attribute access. The string-extraction
helpers :meth:`~turbohtml.Node.re` and :meth:`~turbohtml.Node.re_first` carry over directly, including their ``attr``
keyword for running a pattern over an attribute value instead of the text.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - parsel
      - turbohtml
    - - ``Selector(text=html)``
      - ``turbohtml.parse(html)``
    - - ``sel.css("a")``, ``sel.xpath("//a")``
      - ``node.select("a")``, ``node.xpath("//a")``
    - - ``sel.css("a").get()`` (outer HTML)
      - ``node.select_one("a").html``
    - - ``sel.css("a::text").get()``, ``.getall()``
      - ``node.select_one("a").text``, ``[a.text for a in node.select("a")]``
    - - ``sel.css("a::attr(href)").get()``, ``.getall()``
      - ``node.select_one("a").attr("href")``, ``[a.attr("href") for a in node.select("a")]``
    - - ``sel.xpath("//a/@href").getall()``
      - ``node.xpath("//a/@href")``
    - - ``sel.attrib``
      - ``node.attrs``
    - - ``sel.re(pattern)``, ``sel.re_first(pattern)``
      - ``node.re(pattern)``, ``node.re_first(pattern)``
    - - ``sel.css("a::attr(href)").re(pattern)``
      - ``node.select_one("a").re(pattern, attr="href")``
    - - ``sel.root`` (an lxml element)
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

*************
 Performance
*************

parsel translates every ``.css()`` query to XPath with `cssselect <https://github.com/scrapy/cssselect>`_ and evaluates
it on libxml2, building a fresh ``SelectorList`` on each call. turbohtml compiles a selector against the tree once and
then matches by comparing interned integer atoms, so a reused query costs tens of nanoseconds. The
:doc:`/development/performance` page's *Querying* table benchmarks parsel directly: ``select`` on the ``div a[href]``
query runs roughly thirteen to two hundred times faster in turbohtml, and the tag-only ``find`` runs tens of times
faster.

**********
 Pitfalls
**********

- parsel's ``::text`` and ``::attr()`` pseudo-elements are not CSS standard and turbohtml does not parse them; read
  :attr:`~turbohtml.Node.text` and :meth:`~turbohtml.Element.attr` off the selected node instead.
- ``.get()`` / ``.getall()`` return strings; turbohtml returns nodes, so choose ``.text``, ``.html``,
  :meth:`~turbohtml.Element.attr`, or :meth:`~turbohtml.Node.re` explicitly per call.
- A turbohtml ``xpath("//a/@href")`` already yields the attribute *values* as strings, so there is no ``.getall()`` to
  chain.
- :meth:`~turbohtml.Node.re` and :meth:`~turbohtml.Node.re_first` mirror parsel's regex helpers but run over one node at
  a time rather than a whole ``SelectorList``; map them across :meth:`~turbohtml.Node.select` to cover every match.
  parsel's JSON/JMESPath selectors (``Selector(...).jmespath(...)``) are not ported; run :mod:`json`/``jmespath`` over
  parsed JSON yourself.
