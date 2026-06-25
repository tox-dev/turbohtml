#############
 From parsel
#############

.. image:: https://static.pepy.tech/badge/parsel/month
    :alt: parsel monthly downloads
    :target: https://pepy.tech/project/parsel

`parsel <https://parsel.readthedocs.io>`_ is `Scrapy <https://scrapy.org>`_'s extraction-oriented selector library: a
:class:`~parsel.selector.Selector` query returns a :class:`~parsel.selector.SelectorList` and you pull *strings* out of
it with :meth:`~parsel.selector.SelectorList.get` / :meth:`~parsel.selector.SelectorList.getall`, using the ``::text``
and ``::attr(name)`` pseudo-elements to reach text and attribute values.

***************
 Why turbohtml
***************

turbohtml returns typed :class:`~turbohtml.Node` objects from :meth:`~turbohtml.Node.select` and
:meth:`~turbohtml.Node.xpath`, so the non-standard ``::text`` / ``::attr()`` pseudo-elements become ordinary
:attr:`~turbohtml.Node.text` and :meth:`~turbohtml.Element.attr` access. It compiles a selector against the tree once
and matches by comparing interned integer atoms, where parsel translates every ``.css()`` to XPath with cssselect and
re-evaluates it on libxml2 per call, so a reused query -- and pulling the values out of every match, parsel's whole
point -- runs tens to hundreds of times faster:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - operation
      - turbohtml
      - parsel
    - - select ``div a[href]`` (Daring Fireball, 10 kB)
      - 0.7 µs
      - 34.0 µs (48.4x)
    - - select ``div a[href]`` (Ars Technica, 56 kB)
      - 1.6 µs
      - 148.8 µs (90.6x)
    - - select ``div a[href]`` (Mozilla Blog, 95 kB)
      - 2.4 µs
      - 897.8 µs (378.2x)
    - - select ``div a[href]`` (WHATWG spec, 235 kB)
      - 2.1 µs
      - 1.48 ms (705.8x)
    - - ``@href`` per match (9.6 kB)
      - 0.1 µs
      - 4.3 µs (86.2x)
    - - ``@href`` per match (92 kB)
      - 8.2 µs
      - 222 µs (27.0x)
    - - text per match (92 kB)
      - 8.0 µs
      - 214 µs (26.6x)

*************
 The renames
*************

The string-extraction helpers :meth:`~turbohtml.Node.re` and :meth:`~turbohtml.Node.re_first` carry over directly,
including their ``attr`` keyword for running a pattern over an attribute value instead of the text.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - parsel
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

**********
 Pitfalls
**********

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
