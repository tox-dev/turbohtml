########################
 Migrating to turbohtml
########################

turbohtml replaces the HTML libraries it benchmarks against. None is API-compatible, so porting is a translation:
turbohtml uses one name per concept and a typed shape where those libraries spread the work across aliases, methods, and
treebuilder choices. This page maps each library to turbohtml; `BeautifulSoup
<https://www.crummy.com/software/BeautifulSoup/>`_ gets the deepest treatment because it shares the most surface.

********************
 From BeautifulSoup
********************

Parsing returns a :class:`~turbohtml.Document` instead of a ``BeautifulSoup`` object. There is no parser name to pass,
since turbohtml always runs the WHATWG algorithm:

.. code-block:: python

    # BeautifulSoup
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(markup, "html.parser")

.. testcode::

    from turbohtml import parse
    doc = parse("<p id=intro>Hello</p>")
    print(doc.find("p").attrs["id"])

.. testoutput::

    intro

Bytes work too; pass the raw response and read the resolved encoding back from :attr:`Document.encoding
<turbohtml.Document.encoding>`:

.. testcode::

    doc = parse(b'<meta charset="latin-1"><p>caf\xe9</p>')
    print(doc.find("p").text)
    print(doc.encoding)  # the WHATWG label latin-1 resolves to

.. testoutput::

    café
    windows-1252

Encoding detection
==================

``parse`` runs the WHATWG sniffing algorithm on bytes: a leading BOM, then a ``<meta charset>`` prescan, then a
``windows-1252`` fallback. That covers what ``UnicodeDammit`` reads from the markup, and it stops there -- turbohtml
does not guess an encoding from the byte distribution. ``UnicodeDammit``'s optional statistical pass and the dedicated
detectors (`charset-normalizer <https://github.com/jawah/charset_normalizer>`_, `chardet
<https://github.com/chardet/chardet>`_, ``cchardet``) read byte frequency, so a markup-less stream, or a document with
no BOM and no declaration, lands on ``windows-1252`` here where they would name, say, ``koi8-r``. When there is nothing
to sniff, detect the encoding with ``charset-normalizer`` first and hand turbohtml the decoded ``str`` (or the bytes
with an explicit ``encoding=``).

The renames
===========

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - BeautifulSoup
      - turbohtml
    - - ``tag.name``
      - ``element.tag``
    - - ``tag["class"]``, ``tag.get("x")``, ``tag.has_attr("x")``
      - ``element.attrs["class"]``, ``element.attrs.get("x")``, ``"x" in element.attrs``
    - - ``tag.string``, ``tag.get_text()``
      - ``node.text``, ``node.strings``, ``node.stripped_strings``
    - - ``tag.parents``
      - ``node.ancestors``
    - - ``tag.contents``, ``list(tag.children)``
      - ``node.children``
    - - ``tag.next_elements``
      - ``node.following``
    - - ``tag.find_parent(...)``
      - ``node.find(..., axis=Axis.ANCESTORS)`` or ``node.closest(selector)``
    - - ``tag.find_next(...)``, ``tag.find_previous(...)``
      - ``node.find(..., axis=Axis.FOLLOWING)``, ``node.find(..., axis=Axis.PRECEDING)``
    - - ``tag.find_next_sibling(...)``, ``tag.find_previous_sibling(...)``
      - ``node.find(..., axis=Axis.NEXT_SIBLINGS)``, ``node.find(..., axis=Axis.PREVIOUS_SIBLINGS)``
    - - ``tag.find_all("a", recursive=False)``
      - ``element.find_all("a", axis=Axis.CHILDREN)``
    - - ``soup.select(".cls")``, ``soup.select_one(".cls")``
      - ``node.select(".cls")``, ``node.select_one(".cls")``
    - - ``tag.decompose()``, ``tag.extract()``, ``tag.unwrap()``, ``tag.wrap(...)``
      - ``node.decompose()``, ``node.extract()``, ``node.unwrap()``, ``node.wrap(...)``
    - - ``tag.insert_before(...)``, ``tag.insert_after(...)``, ``tag.replace_with(...)``
      - the same names on every :class:`~turbohtml.Node`
    - - ``soup.new_tag("div")``, ``soup.new_string("hi")``
      - ``Element("div")``, ``Text("hi")``
    - - ``tag.prettify()``
      - ``node.serialize(layout=Indent(2))``
    - - ``tag.smooth()``
      - ``element.normalize()``
    - - ``tag.sourceline``, ``tag.sourcepos``
      - ``node.source_line``, ``node.source_col`` (same 1-based line, 0-based column; ``None`` when absent)

Searching
=========

The ``find``/``find_all`` filter grammar covers what ``bs4`` spread across many methods. A keyword filter matches an
attribute; ``class_`` and ``attrs`` match the rest; ``axis`` replaces the directional finders and ``recursive=False``:

.. testcode::

    from turbohtml import Axis
    doc = parse('<ul><li class="x">a</li><li class="y">b</li></ul>')
    print([li.text for li in doc.find_all("li")])
    print(doc.find("li", class_="y").text)
    print(doc.find("ul").find_all("li", axis=Axis.CHILDREN, attrs={"class": "x"}))

.. testoutput::

    ['a', 'b']
    b
    [Element('li')]

``Axis`` reaches every direction a ``bs4`` directional finder did:

.. testcode::

    deep = parse("<section><p><b>hi</b></p></section>").find("b")
    print(deep.find("section", axis=Axis.ANCESTORS).tag)
    print(deep.closest("section").tag)

.. testoutput::

    section
    section

Attributes and text
===================

``.attrs`` is the single access point; there is no ``tag["x"]`` shortcut, because ``node[i]`` indexes child nodes.
Multi-valued attributes (``class``, ``rel``, ...) read back as a ``list[str]``, and text is real child nodes (the WHATWG
DOM shape), so there is no ``.string`` shortcut and no `lxml <https://lxml.de>`_-style ``text``/``tail`` split:

.. testcode::

    a = parse('<a class="btn lg" href="/x">go</a>').find("a")
    print(a.attrs["class"])
    print(a[0])  # indexing reaches children, never attributes
    p = parse("<p>Hello <b>bold</b> world</p>").find("p")
    print((p.text, list(p.stripped_strings)))

.. testoutput::

    ['btn', 'lg']
    Text('go')
    ('Hello bold world', ['Hello', 'bold', 'world'])

Output
======

The default serialization is WHATWG-conformant, so it differs from ``bs4``'s ``html`` formatter on named entities,
attribute order, and ``<br>`` versus ``<br/>``. Choose ``Formatter.NAMED_ENTITIES`` to approximate ``bs4``:

.. testcode::

    from turbohtml import Formatter
    node = parse("<p>café &amp; co</p>").find("p")
    print(node.html)
    print(node.serialize(formatter=Formatter.NAMED_ENTITIES))

.. testoutput::

    <p>café &amp; co</p>
    <p>caf&eacute; &amp; co</p>

Pitfalls
========

- ``node[i]`` indexes children; attributes are reached through ``.attrs``, never ``node["attr"]``.
- Text is real child nodes, so there is no ``.string`` shortcut and no ``text``/``tail``; iterate the children.
- Default output is WHATWG-conformant; pick ``Formatter.NAMED_ENTITIES`` to come close to ``bs4``'s ``html`` formatter.
- ``==`` compares identity, so two trees with the same markup are unequal. Where ``bs4`` code leaned on ``==`` between
  trees, compare serializations (``a.html == b.html``) or walk the nodes.

***********
 From lxml
***********

:func:`turbohtml.parse` replaces ``lxml.html.document_fromstring`` and returns a :class:`~turbohtml.Document`;
:func:`turbohtml.parse_fragment` replaces ``lxml.html.fromstring`` for a fragment. The biggest change is the tree shape:
lxml stores text as an element's ``.text`` and ``.tail`` strings, while turbohtml models it as real child
:class:`~turbohtml.Text` nodes, so you iterate children instead of reading two string fields.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - lxml
      - turbohtml
    - - ``el.tag``
      - ``el.tag`` (same)
    - - ``el.get("x")``, ``el.attrib``, ``el.set("x", "v")``
      - ``el.attrs.get("x")``, ``el.attrs``, ``el.attrs["x"] = "v"``
    - - ``el.text``, ``el.tail``
      - child :class:`~turbohtml.Text` nodes; iterate ``el.children``
    - - ``el.text_content()``
      - ``el.text``
    - - ``el.getparent()``, ``el.getnext()``, ``el.getprevious()``
      - ``el.parent``, ``el.next_sibling``, ``el.previous_sibling``
    - - ``list(el)``, ``el.iterdescendants()``, ``el.iterancestors()``
      - ``el.children``, ``el.descendants``, ``el.ancestors``
    - - ``el.findall(".//a")``, ``el.xpath("//a[@href]")``
      - ``el.find_all("a")``, ``el.find_all("a", attrs={"href": True})``
    - - ``el.cssselect("div a")``
      - ``el.select("div a")``
    - - ``lxml.html.Element("div")``, ``etree.SubElement(p, "div")``
      - ``Element("div")``, ``p.append(Element("div"))``
    - - ``el.drop_tag()``, ``el.drop_tree()``
      - ``el.unwrap()``, ``el.decompose()``
    - - ``el.sourceline``
      - ``el.source_line`` (1-based, like lxml; plus ``el.source_col`` for the 0-based column lxml lacks)
    - - ``el.iterlinks()``
      - :meth:`el.links() <turbohtml.Node.links>`
    - - ``el.make_links_absolute(base)``, ``el.rewrite_links(fn)``
      - :meth:`el.resolve_links(base) <turbohtml.Node.resolve_links>`, :meth:`el.rewrite_links(fn)
        <turbohtml.Node.rewrite_links>`
    - - ``lxml.html.tostring(el)``
      - ``el.html``

.. testcode::

    doc = parse('<div><a href="/x">go</a></div>')
    print(doc.find_all("a", attrs={"href": True}))
    print(doc.select_one("div a").attrs["href"])

.. testoutput::

    [Element('a')]
    /x

Pitfalls
========

- No XPath. Use the ``find``/``find_all`` filter grammar (an ``axis``, ``attrs``, and string, regex, or callable
  filters) or CSS :meth:`~turbohtml.Node.select`.
- No ``text``/``tail``. A node's children are its text runs and elements interleaved; read :attr:`~turbohtml.Node.text`
  for the concatenation.
- lxml parses with libxml2, which is not WHATWG-conformant, so malformed input lands in a different tree than the one
  turbohtml (and a browser) builds.
- For a document that arrives in pieces, ``etree.iterparse`` is replaced by :class:`turbohtml.IncrementalParser`: feed
  ``str`` or ``bytes`` chunks with ``feed`` and call ``close`` for the finished :class:`~turbohtml.Document`. The parser
  never holds the whole source at once, so you can parse a stream larger than the source buffer you would otherwise
  materialize for :func:`turbohtml.parse`.

*****************
 From selectolax
*****************

`selectolax <https://github.com/rushter/selectolax>`_ wraps the same `lexbor <https://lexbor.com>`_ engine turbohtml
benchmarks against, so the speed is comparable; the move is mostly API surface. selectolax searches with CSS only and
exposes ``text()`` as a method, while turbohtml adds the ``find``/``find_all`` filter grammar and makes
:attr:`~turbohtml.Node.text` a property.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - selectolax
      - turbohtml
    - - ``LexborHTMLParser(html)``
      - ``turbohtml.parse(html)``
    - - ``parser.root``, ``parser.body``
      - ``doc.root``, ``doc.find("body")``
    - - ``node.css("a")``, ``node.css_first("a")``
      - ``node.select("a")``, ``node.select_one("a")``
    - - ``node.tag``
      - ``node.tag`` (same)
    - - ``node.attributes``
      - ``node.attrs``
    - - ``node.text()`` (a method)
      - ``node.text`` (a property), ``node.strings``, ``node.stripped_strings``
    - - ``node.html``, ``node.decompose()``, ``node.unwrap()``
      - the same names

.. testcode::

    doc = parse("<ul><li>a</li><li>b</li></ul>")
    print([li.text for li in doc.select("li")])

.. testoutput::

    ['a', 'b']

Pitfalls
========

- selectolax queries are CSS-only; turbohtml adds the ``find``/``find_all`` filter grammar with axes and regex or
  callable filters.
- ``node.text`` is a property; drop the parentheses.
- selectolax mutation is limited; turbohtml's edit surface (``append``, ``insert``, ``wrap``, ``unwrap``,
  ``replace_with``, and the rest) is full.

******************
 From resiliparse
******************

`resiliparse <https://github.com/chatnoir-eu/chatnoir-resiliparse>`_ wraps the same `lexbor <https://lexbor.com>`_
engine selectolax does, so like turbohtml it builds a WHATWG tree with real text nodes; the move is API surface. Its
``HTMLTree`` exposes DOM-style traversal and both ``get_element_by_*`` lookups and CSS ``query_selector`` methods, which
turbohtml folds into the one ``find``/``find_all``/``select`` grammar.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - resiliparse
      - turbohtml
    - - ``HTMLTree.parse(html)``
      - ``turbohtml.parse(html)``
    - - ``tree.body``, ``tree.head``, ``tree.title``
      - ``doc.find("body")``, ``doc.find("head")``, ``doc.find("title").text``
    - - ``node.query_selector("a")``, ``node.query_selector_all("a")``
      - ``node.select_one("a")``, ``node.select("a")``
    - - ``node.get_element_by_id("main")``
      - ``node.find(id="main")``
    - - ``node.get_elements_by_tag_name("a")``, ``node.get_elements_by_class_name("x")``
      - ``node.find_all("a")``, ``node.find_all(class_="x")``
    - - ``node.getattr("href")``, ``node.hasattr("href")``, ``node.setattr(...)``, ``node.delattr(...)``
      - ``node.attrs.get("href")``, ``"href" in node.attrs``, ``node.attrs[...] = ...``, ``del node.attrs[...]``
    - - ``node.tag``, ``node.text``, ``node.html``, ``node.class_list``
      - ``node.tag``, ``node.text``, ``node.html``, ``node.attrs["class"]``
    - - ``node.parent``, ``node.next_element``, ``node.prev_element``, ``node.first_element_child``
      - ``node.parent``, ``node.next_sibling``, ``node.previous_sibling``, ``node.first_child``
    - - ``tree.create_element("div")``, ``node.append_child(...)``, ``node.decompose()``
      - ``Element("div")``, ``node.append(...)``, ``node.decompose()``
    - - ``extract_plain_text(html)`` (from ``resiliparse.extract``)
      - ``node.to_text()`` for layout, ``node.text`` for the raw concatenation

.. testcode::

    doc = parse("<div id=main><p class=x>Hi <a href='/u'>link</a></p></div>")
    print(doc.select_one("#main a").attrs["href"])
    print(doc.find(id="main").find("p").text)

.. testoutput::

    /u
    Hi link

Pitfalls
========

- resiliparse's boilerplate and main-content extraction, language detection, and the encoding and archive utilities it
  ships for web-crawl processing have no turbohtml equivalent; the overlap is parsing and DOM access only.
- turbohtml compares nodes by value, not identity, so ``find("p") == find("p")`` is ``False`` where resiliparse returns
  the same wrapped node; compare serializations or walk the tree instead.

A note on gumbo
===============

`gumbo <https://github.com/google/gumbo-parser>`_, the C WHATWG parser behind `html5-parser
<https://html5-parser.readthedocs.io>`_, has no migration table here. It is read-oriented and archived upstream, and its
Python binding no longer builds on a current toolchain, so there is nothing to port from in practice. Code that read a
gumbo tree maps onto the same :meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.select` read surface shown above, with
turbohtml supplying the maintained, mutable, typed tree that lineage lacks. The maintained `html5-parser
<https://github.com/kovidgoyal/html5-parser>`_ wraps the same gumbo engine and has its own section below.

*******************
 From html5-parser
*******************

`html5-parser <https://github.com/kovidgoyal/html5-parser>`_ wraps gumbo, the C WHATWG parser, and hands the result back
as an `lxml <https://lxml.de>`_/ElementTree tree. It is turbohtml's closest direct competitor: a native parse with no
pure-Python pass. The difference is what you get back. ``html5_parser.parse`` returns a read-oriented lxml element built
on ``libxml2``, while :func:`turbohtml.parse` returns a :class:`~turbohtml.Document` with a mutable, natively typed tree
and a real ``<template>`` content document, and no ``libxml2``/gumbo build dependency.

.. code-block:: python

    # html5-parser
    from html5_parser import parse

    root = parse(markup)  # an lxml.etree element

.. testcode::

    from turbohtml import parse
    doc = parse("<table><tr><td>cell</td></table>")
    print(doc.find("td").text)  # the tbody the WHATWG algorithm inserts is walked the same way

.. testoutput::

    cell

Because the tree it returns is lxml's, the element accessors port exactly as in the `From lxml`_ section above:
``el.get``/``el.attrib`` become ``el.attrs``, the ``el.text``/``el.tail`` string pair becomes child
:class:`~turbohtml.Text` nodes, and ``el.getparent()`` becomes ``el.parent``. The one accessor with no equivalent is
``el.xpath(...)``: html5-parser inherits ``libxml2``'s XPath, while turbohtml searches with CSS through
:meth:`~turbohtml.Node.select` and the :meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.find_all` grammar. The
:doc:`performance` page benchmarks the WHATWG tree builders; html5-parser sits in the same native-gumbo tier as the C
parsers measured there, so it is cross-linked rather than given a separate row, since its lxml tree is bound to a
specific ``libxml2`` build that the benchmark cannot pin portably.

*************
 From parsel
*************

`parsel <https://github.com/scrapy/parsel>`_ (Scrapy's selector library) is extraction-oriented: a ``Selector`` query
returns a ``SelectorList`` and you pull *strings* out of it with ``.get()`` / ``.getall()``, using the ``::text`` and
``::attr(name)`` pseudo-elements to reach text and attribute values. turbohtml instead returns :class:`~turbohtml.Node`
objects from :meth:`~turbohtml.Node.select` and :meth:`~turbohtml.Node.xpath`, and you read
:attr:`~turbohtml.Node.text`, ``attrs``, or :attr:`~turbohtml.Node.html` off each node -- so the non-standard ``::text``
/ ``::attr()`` pseudo-elements become ordinary attribute and text access.

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
      - ``node.select_one("a").attrs.get("href")``, ``[a.attrs.get("href") for a in node.select("a")]``
    - - ``sel.xpath("//a/@href").getall()``
      - ``node.xpath("//a/@href")``
    - - ``sel.attrib``
      - ``node.attrs``
    - - ``sel.re(pattern)``, ``sel.re_first(pattern)``
      - ``re`` over ``node.text`` (use Python's :mod:`re` directly)
    - - ``sel.root`` (an lxml element)
      - the :class:`~turbohtml.Node` itself

.. testcode::

    doc = parse('<a href="/x">home</a><a href="/y">about</a>')
    print([a.attrs.get("href") for a in doc.select("a")])
    print(doc.select_one("a").text)
    print(doc.xpath("//a/@href"))

.. testoutput::

    ['/x', '/y']
    home
    ['/x', '/y']

Performance
===========

parsel translates every ``.css()`` query to XPath with `cssselect <https://github.com/scrapy/cssselect>`_ and evaluates
it on libxml2, building a fresh ``SelectorList`` on each call. turbohtml compiles a selector against the tree once and
then matches by comparing interned integer atoms, so a reused query costs tens of nanoseconds. The :doc:`performance`
page's *Querying* table benchmarks parsel directly: ``select`` on the ``div a[href]`` query runs roughly thirteen to two
hundred times faster in turbohtml, and the tag-only ``find`` runs tens of times faster.

Pitfalls
========

- parsel's ``::text`` and ``::attr()`` pseudo-elements are not CSS standard and turbohtml does not parse them; read
  :attr:`~turbohtml.Node.text` and ``attrs`` off the selected node instead.
- ``.get()`` / ``.getall()`` return strings; turbohtml returns nodes, so choose ``.text``, ``.html``, or an attribute
  explicitly per call.
- A turbohtml ``xpath("//a/@href")`` already yields the attribute *values* as strings, so there is no ``.getall()`` to
  chain.

***************
 From html5lib
***************

`html5lib <https://github.com/html5lib/html5lib-python>`_ runs the same WHATWG algorithm turbohtml does, so the *tree*
it produces matches; what changes is that html5lib hands you a generic tree you select with a treebuilder (an
:mod:`xml.etree.ElementTree` element by default, or DOM, or lxml), while turbohtml has one typed hierarchy with
navigation, search, and serialization built in.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - html5lib
      - turbohtml
    - - ``html5lib.parse(s)``
      - ``turbohtml.parse(s)``
    - - ``html5lib.parse(s, treebuilder="dom")``
      - one typed tree, no treebuilder choice
    - - ``html5lib.parseFragment(s, container="div")``
      - ``turbohtml.parse_fragment(s, "div")``
    - - the html5lib tokenizer
      - ``turbohtml.tokenize(s)``, ``turbohtml.Tokenizer``
    - - ``el.tag`` namespaced (``{http://www.w3.org/1999/xhtml}div``)
      - ``el.tag`` plus an :class:`~turbohtml.Namespace` on ``el.namespace``
    - - the treebuilder's own walk and ``el.attrib``
      - ``el.children``, ``el.find``/``el.select``, ``el.attrs``

.. testcode::

    doc = parse("<table><tr><td>x")  # the same tree html5lib and a browser build
    print(doc.find("td").text)

.. testoutput::

    x

Pitfalls
========

- html5lib gives you a foreign tree (ElementTree, DOM, or lxml) and you pick a treebuilder; turbohtml has one typed
  tree, so there is nothing to choose and the node types are sealed and pattern-matchable.
- html5lib's ElementTree output namespaces names; turbohtml keeps ``tag`` plain and carries the namespace separately as
  :attr:`~turbohtml.Element.namespace`.

**************
 From pyquery
**************

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

Two limits are worth stating. pyquery exposes lxml's ``.xpath(...)``; turbohtml's ``Query`` is CSS-only, so an XPath
chain moves to a selector or to the node-level :meth:`~turbohtml.Node.find` grammar. And jQuery methods turbohtml does
not mirror (DOM-mutation helpers like ``.wrap_all`` or pyquery's network-fetching constructor) drop down to the node API
on :meth:`Query.items <turbohtml.query.Query.items>`. The :doc:`performance` page's fluent-chaining benchmark times the
same chain against pyquery.

***************************
 From the standard library
***************************

:func:`turbohtml.escape` and :func:`turbohtml.unescape` reproduce :func:`python:html.escape` and
:func:`python:html.unescape` byte for byte, so they are a drop-in:

.. testcode::

    import html
    from turbohtml import escape, unescape
    print(escape('<a href="x">') == html.escape('<a href="x">'))
    print(unescape("caf&eacute; &#127881;") == html.unescape("caf&eacute; &#127881;"))

.. testoutput::

    True
    True

To keep an existing :class:`python:html.parser.HTMLParser` subclass, swap its base class for
:class:`turbohtml.html_parser.HTMLParser`: the same ``handle_*`` callbacks and ``feed``/``close`` methods run over the
WHATWG-conformant tokenizer. Or drop the subclass and take the token stream from :func:`turbohtml.tokenize` (or
:meth:`turbohtml.Tokenizer.feed` for incremental input), or skip tokens entirely and :func:`turbohtml.parse` straight to
a tree. All three are WHATWG-conformant, unlike ``html.parser``. The :doc:`how-to` guide has a worked port.

``HTMLParser`` is a SAX-style callback API; turbohtml gives you the events as a token stream you drive yourself, which
inverts the control flow. Each ``handle_*`` override becomes a branch on :attr:`Token.type <turbohtml.Token.type>`:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - ``html.parser`` callback
      - turbohtml token
    - - ``handle_starttag(tag, attrs)``
      - ``token.type is TokenType.START_TAG`` → ``token.tag``, ``token.attrs``
    - - ``handle_startendtag(tag, attrs)``
      - ``TokenType.START_TAG`` with ``token.self_closing``
    - - ``handle_endtag(tag)``
      - ``TokenType.END_TAG`` → ``token.tag``
    - - ``handle_data(data)``
      - ``TokenType.TEXT`` → ``token.data``
    - - ``handle_comment(data)``
      - ``TokenType.COMMENT`` → ``token.data``
    - - ``handle_decl(decl)``
      - ``TokenType.DOCTYPE`` → ``token.name``
    - - ``handle_entityref``/``handle_charref``
      - ``tokenize(..., resolve_references=False)`` → ``TokenType.CHARACTER_REFERENCE``, else resolved in ``token.data``
    - - ``get_starttag_text()``
      - ``tokenize(..., capture_source=True)`` → ``token.source``

.. testcode::

    import turbohtml
    from turbohtml import TokenType

    events = []
    for token in turbohtml.tokenize('<p class="x">Hi &amp; bye</p>'):
        if token.type is TokenType.START_TAG:
            events.append(("start", token.tag, token.attrs))
        elif token.type is TokenType.TEXT:
            events.append(("data", token.data))
        elif token.type is TokenType.END_TAG:
            events.append(("end", token.tag))
    print(events)

.. testoutput::

    [('start', 'p', [('class', 'x')]), ('data', 'Hi & bye'), ('end', 'p')]

By default ``token.data`` already holds decoded text (``Hi & bye`` above, not ``Hi &amp; bye``), the equivalent of
``convert_charrefs=True``. To recover the split stream that ``convert_charrefs=False`` gives - one event per character
reference - pass ``resolve_references=False`` and handle ``TokenType.CHARACTER_REFERENCE`` tokens, whose
``token.source`` is the verbatim reference (``&amp;``) and ``token.data`` its resolved value. The verbatim start-tag
text that ``get_starttag_text()`` returns is ``token.source`` once you pass ``capture_source=True``. When the goal is
the resulting structure rather than the event sequence, skip the loop and :func:`turbohtml.parse` to a tree, then walk
it. The :doc:`performance` page's tokenizing benchmark times this token loop against ``html.parser``.

*********************
 From w3lib (Scrapy)
*********************

`w3lib <https://github.com/scrapy/w3lib>`_ collects the web utilities Scrapy reuses. Only its ``w3lib.html`` text/entity
subset overlaps with turbohtml; the URL canonicalization, response-encoding, and HTTP helpers in ``w3lib.url`` and
elsewhere stay outside turbohtml's scope and have no equivalent here.

``replace_entities`` resolves character references the same way :func:`turbohtml.unescape` does, so it is a drop-in;
``w3lib.html.replace_entities("caf&eacute; &amp; co")`` returns the same string this prints:

.. testcode::

    from turbohtml import unescape
    print(unescape("caf&eacute; &amp; co"))

.. testoutput::

    café & co

The tag and comment strippers map onto parsing to a real tree and reading its text. ``remove_tags`` becomes
:func:`turbohtml.parse` followed by :attr:`~turbohtml.Node.text`, and ``remove_comments`` needs nothing extra because
comments never appear in ``text``:

.. testcode::

    from turbohtml import parse
    print(parse("<p>Tom &amp; Jerry <b>says</b> hi</p><!--note-->").text)

.. testoutput::

    Tom & Jerry says hi

The behavior differs in one way worth knowing before migrating: ``remove_tags`` strips angle brackets with a regular
expression and leaves entities encoded (``Tom &amp; Jerry``), while ``text`` runs the WHATWG tree builder and returns
decoded characters (``Tom & Jerry``). turbohtml parses malformed and nested markup the way a browser does rather than
matching ``<...>`` spans, so the two diverge on inputs a regex misreads. ``remove_tags_with_content``, which drops a tag
together with its subtree, has no single call: select the subtrees to keep with :meth:`~turbohtml.Node.find_all` and
join their ``text``, the allowlist-style filtering the :doc:`how-to` guide covers, or reach for ``turbohtml.sanitizer``
when the goal is producing safe HTML rather than plain text.

The two helpers that read a document's own URL hints, ``get_base_url`` and ``get_meta_refresh``, map to the
:meth:`~turbohtml.Document.base_url` and :meth:`~turbohtml.Document.meta_refresh` methods on the parsed document. Each
takes the fallback base URL w3lib calls ``baseurl`` and resolves the hint against it:

.. testcode::

    from turbohtml import parse

    doc = parse('<base href="/sub/"><meta http-equiv=refresh content="5; url=next.html">')
    print(doc.base_url("http://site.com/"))
    print(doc.meta_refresh("http://site.com/"))

.. testoutput::

    http://site.com/sub/
    (5.0, 'http://site.com/next.html')

*****************
 From markupsafe
*****************

``turbohtml.markup`` is a drop-in for `markupsafe <https://markupsafe.palletsprojects.com>`_'s public surface, so a
`Jinja2 <https://jinja.palletsprojects.com>`_, `WTForms <https://wtforms.readthedocs.io>`_, or `Werkzeug
<https://werkzeug.palletsprojects.com>`_ project changes only the import line:

.. code-block:: python

    # markupsafe
    from markupsafe import Markup, escape, escape_silent, soft_str, EscapeFormatter

    # turbohtml
    from turbohtml.markup import Markup, escape, escape_silent, soft_str, EscapeFormatter

``escape`` returns a :class:`~turbohtml.markup.Markup` with the same numeric quote references markupsafe emits, honors
the ``__html__`` protocol, and leaves an existing ``Markup`` untouched. ``Markup`` overrides the full :class:`str`
method surface, so a value that flows through a template filter such as ``upper`` or ``replace`` stays a ``Markup`` and
autoescaping does not escape it a second time. The operations that combine text (``+``, ``%``,
:meth:`~turbohtml.markup.Markup.format`, :meth:`~turbohtml.markup.Markup.join`, ``replace``, ...) escape their untrusted
operands:

.. testcode::

    from turbohtml.markup import Markup, escape, escape_silent

    print(escape('<a href="x">Tom & Jerry</a>'))
    print(Markup("<b>{}</b>").format("<i>"))
    print(Markup("<b>safe</b>").upper())  # str methods keep the Markup, so it is not re-escaped
    print(escape_silent(None) == Markup(""))

.. testoutput::

    &lt;a href=&#34;x&#34;&gt;Tom &amp; Jerry&lt;/a&gt;
    <b>&lt;i&gt;</b>
    <B>SAFE</B>
    True

Two methods are upgrades rather than reimplementations: :meth:`~turbohtml.markup.Markup.striptags` and
:meth:`~turbohtml.markup.Markup.unescape` run on turbohtml's tokenizer and HTML5 reference resolution, so they are
faster and resolve references markupsafe's regex-based stripping can miss.

These differences from markupsafe do not affect migration: the escape runs in C, every ``Markup`` method runs faster
than markupsafe's, the ``soft_unicode`` alias that markupsafe 3.0 removed is absent here too, and turbohtml does not
register itself as ``markupsafe``, so adoption stays an explicit per-project import.

***********************
 From bleach (linkify)
***********************

`bleach <https://github.com/mozilla/bleach>`_ is end of life and has no successor for its linkifier, so
``turbohtml.linkify`` takes its place. The entry points keep bleach's names, so the import changes and the common case
is identical:

.. code-block:: python

    # bleach
    from bleach import linkify
    from bleach.linkifier import Linker, DEFAULT_CALLBACKS
    from bleach.callbacks import nofollow, target_blank

    # turbohtml
    from turbohtml.linkify import linkify, Linker, DEFAULT_CALLBACKS, nofollow, target_blank

``linkify(text, callbacks=..., skip_tags=..., parse_email=...)``, the reusable :class:`~turbohtml.linkify.Linker`, and
the ``nofollow``/``target_blank`` defaults work as before. Only custom callbacks change shape. bleach passed ``(attrs,
new)`` where ``attrs`` was keyed by ``(namespace, name)`` tuples with a ``"_text"`` pseudo-key for the visible text;
turbohtml passes a single :class:`~turbohtml.linkify.Link` with plain ``url``, ``text``, and ``attrs`` (a ``dict[str,
str]``), and a callback returns it to keep the link or ``None`` to leave the text bare. bleach's ``new`` flag becomes
``Link.existing`` (inverted: ``new=True`` is ``existing=False``). Porting a callback means reading fields instead of
tuple keys:

.. testcode::

    from turbohtml.linkify import linkify, Link

    def shorten(link: Link) -> Link | None:
        link.text = link.url.removeprefix("https://").removeprefix("http://")
        return link

    print(linkify("read https://example.com/page", callbacks=[shorten]))

.. testoutput::

    read <a href="https://example.com/page">example.com/page</a>

One default differs from bleach, deliberately: turbohtml leaves an existing ``<a>`` untouched so linkifying is
idempotent, where bleach always reprocessed present links. Opt back in with ``process_existing=True`` to run the
callbacks over author-written anchors too (the callback reads ``link.existing`` to branch). bleach's ``protocols`` maps
to ``schemes``, which restricts the explicit URL schemes that autolink, and bleach's custom-TLD support maps to
``extra_tlds``, on top of a current IANA table you can regenerate where bleach shipped a frozen list. A bare domain such
as ``example.com`` still links only when its last label is a known TLD. The scan for link candidates runs in C, so
linkifying a page is faster than bleach's html5lib-based pass.

********************
 From linkify-it-py
********************

`linkify-it-py <https://github.com/tsutsu3/linkify-it-py>`_ scans plain text and returns the link spans it finds;
turning them into ``<a>`` tags, and skipping text that is already markup, is left to the caller. turbohtml does both,
through two entry points. To rewrite HTML, use :func:`~turbohtml.linkify.linkify`. To match spans the way
linkify-it-py's ``match`` does, use :class:`~turbohtml.linkify.Detector`; where linkify-it-py returns ``Match`` objects
with ``index``/``last_index``/``url``/``schema``, turbohtml returns :class:`~turbohtml.linkify.LinkSpan` objects with
``start``/``end``/``url``/``text``:

.. code-block:: python

    # linkify-it-py
    from linkify_it import LinkifyIt

    matches = LinkifyIt().match("see https://example.com")
    # [Match(url="https://example.com", index=4, last_index=23, ...)] or None

.. testcode::

    from turbohtml.linkify import Detector

    span = Detector().find("see https://example.com")[0]
    print(span.start, span.end, span.url)

.. testoutput::

    4 23 https://example.com

linkify-it-py's ``test`` becomes :meth:`~turbohtml.linkify.Detector.has_link`, ``add(schema, rule)`` becomes the
``schemes`` argument for scheme-less schemes, and ``tlds(...)`` becomes the ``tlds`` argument. Because
:func:`~turbohtml.linkify.linkify` parses the input as HTML, it also leaves alone a URL already inside an ``<a>`` or a
``<script>`` that linkify-it-py, working on the raw string, would match again. linkify-it-py still reaches further into
fuzzy IP and email heuristics; turbohtml covers the common web, ``mailto:``, bare-domain, and registered-scheme cases
and trades that breadth for being HTML-aware and several times faster.

*********************
 From bleach (clean)
*********************

bleach is end of life and has no maintained successor for its sanitizer, so ``turbohtml.sanitizer`` takes its place. The
bleach-compatible shim keeps ``clean``'s signature so the import is the only change:

.. code-block:: python

    # bleach
    from bleach import clean

    # turbohtml
    from turbohtml.bleach_compat import clean

``clean(text, tags=..., attributes=..., protocols=..., strip=..., strip_comments=...)`` maps onto a
:class:`~turbohtml.sanitizer.Policy`. ``attributes`` accepts bleach's list, per-tag dict, or callable forms; ``strip``
chooses between dropping a disallowed tag and keeping its children (``True``) and escaping it (``False``, the default):

.. testcode::

    from turbohtml.bleach_compat import clean

    print(clean("<p>Hi <a href='http://x'>link</a></p><script>evil()</script>"))

.. testoutput::

    &lt;p&gt;Hi <a href="http://x">link</a>&lt;/p&gt;&lt;script&gt;evil()&lt;/script&gt;

For new code prefer the native :class:`~turbohtml.sanitizer.Policy`/:class:`~turbohtml.sanitizer.Sanitizer` API: a
frozen, thread-safe policy (bleach's ``clean`` had a documented thread-safety footgun), an
:class:`~turbohtml.sanitizer.OnDisallowed` enum that names escape/strip/remove where bleach overloaded two booleans, and
an ``attribute_filter`` that rewrites or drops a value where bleach's callable only returned a bool. One difference is
deliberate and load-bearing: turbohtml's safety baseline (``<script>``, ``on*`` handlers, ``javascript:`` URLs) is not
configurable, so even a permissive ``attributes`` callable cannot re-admit them, where bleach faithfully kept whatever
you allowed. The native sanitizer scrubs the ``style`` attribute against ``Policy.css_properties`` (the safe set
bleach's ``css_sanitizer`` defaults to), so an allowed ``style`` keeps only allowlisted declarations; the
bleach-compatible ``clean`` shim does not yet take a ``css_sanitizer`` argument (it raises), and ``<style>`` element
contents are dropped rather than scrubbed.

**********
 From nh3
**********

`nh3 <https://github.com/messense/nh3>`_, the Rust ammonia binding, is the other bleach-refugee target, but it declined
bleach feature parity: it has no escape-instead-of-strip mode, no attribute-rewriting callable, and no linkifier.
``turbohtml.sanitizer`` covers those while staying in the same performance tier:

.. code-block:: python

    # nh3
    import nh3

    nh3.clean(text, tags={"a"}, attributes={"a": {"href"}})

    # turbohtml
    from turbohtml.sanitizer import sanitize, Policy

    sanitize(text, Policy(tags=frozenset({"a"}), attributes={"a": frozenset({"href"})}))

nh3's ``link_rel`` maps to ``Policy.add_link_rel``, its ``url_schemes`` to ``url_schemes``, and its ``attribute_filter``
to ``attribute_filter`` (turbohtml's may rewrite a value, not only drop it). Its ``set_tag_attribute_values`` (force an
attribute onto matching tags) maps to ``Policy.set_attributes``. turbohtml escapes disallowed tags by default
(``OnDisallowed.ESCAPE``), the mode ammonia blocked upstream; pass ``OnDisallowed.STRIP`` or ``OnDisallowed.REMOVE`` for
nh3-style dropping.

**********************
 From lxml-html-clean
**********************

`lxml-html-clean <https://github.com/fedora-python/lxml_html_clean>`_ (the ``Cleaner`` split out of ``lxml.html.clean``)
takes the opposite stance to ``turbohtml.sanitizer``: it is a **blocklist**. You toggle off categories of dangerous
content (``scripts``, ``javascript``, ``style``, ``comments``, ``embedded``, ``frames``, ``forms``, ``meta``, ...) and
everything else survives, so a tag the library has not heard of passes through. turbohtml is an **allowlist**: nothing
survives unless a :class:`~turbohtml.sanitizer.Policy` names it, which is why the safety baseline holds against markup
the author never anticipated.

Porting inverts the model. Instead of switching dangerous things off, declare the small set you keep:

.. code-block:: python

    # lxml-html-clean: enumerate what to strip, keep the rest
    from lxml_html_clean import Cleaner

    Cleaner(
        scripts=True, javascript=True, comments=True, style=True, forms=True
    ).clean_html(text)

.. testcode::

    from turbohtml.sanitizer import sanitize, Policy

    print(sanitize(
        "<p>Hi<script>x()</script> <a href='javascript:1'>l</a></p>",
        Policy(tags=frozenset({"p", "a"}), attributes={"a": frozenset({"href"})}),
    ))

.. testoutput::

    <p>Hi&lt;script&gt;x()&lt;/script&gt; <a>l</a></p>

The ``javascript:`` URL is gone because ``http``/``https``/``mailto`` are the only schemes the policy admits, and the
``<script>`` is escaped rather than executed. ``Cleaner``'s ``host_whitelist`` and ``allow_tags`` lists fold into
``Policy.tags`` and ``attribute_filter``, its ``kill_tags`` (drop the element together with its content) maps to
``Policy.remove_with_content``, and its ``add_nofollow`` maps to ``Policy.add_link_rel``. turbohtml scrubs a kept
``style`` attribute against ``Policy.css_properties``, though it drops ``<style>`` elements where ``Cleaner`` scrubs
their text too, and ``Cleaner`` rewrites a disallowed scheme to an empty ``href`` where turbohtml drops the attribute
outright.

*********************
 From html-sanitizer
*********************

`html-sanitizer <https://github.com/matthiask/html-sanitizer>`_ already shares turbohtml's allowlist stance, so the move
is a settings-to-:class:`~turbohtml.sanitizer.Policy` translation rather than a rethink. Its ``settings`` dict carries
``tags`` (a set), ``attributes`` (a per-tag dict), ``add_nofollow``, and a ``sanitize_href`` scheme check:

.. code-block:: python

    # html-sanitizer
    from html_sanitizer import Sanitizer

    Sanitizer(
        {"tags": {"a", "p"}, "attributes": {"a": {"href"}}, "add_nofollow": True}
    ).sanitize(text)

.. testcode::

    from turbohtml.sanitizer import sanitize, Policy

    print(sanitize(
        '<p>Hi <a href="http://x">l</a></p>',
        Policy(
            tags=frozenset({"p", "a"}),
            attributes={"a": frozenset({"href"})},
            add_link_rel=frozenset({"nofollow"}),
        ),
    ))

.. testoutput::

    <p>Hi <a href="http://x" rel="nofollow">l</a></p>

``tags`` maps to ``Policy.tags``, ``attributes`` to ``Policy.attributes``, ``add_nofollow`` to
``add_link_rel={"nofollow"}``, and ``sanitize_href``'s allowed schemes to ``url_schemes``. Two html-sanitizer features
have no direct port: the whitespace normalization and tag-merging it performs (``empty``, ``separate``, ``whitespace``)
and its ``element_preprocessors``/``element_postprocessors`` hooks. turbohtml's ``attribute_filter`` covers value-level
rewriting, but structural post-processing is left to a walk over the returned tree. html-sanitizer parses through lxml;
turbohtml runs the WHATWG tree builder in C.

******************************
 From html2text / markdownify
******************************

`html2text <https://github.com/Alir3z4/html2text>`_ and `markdownify
<https://github.com/matthewwithanm/python-markdownify>`_ both turn HTML into Markdown.
:meth:`~turbohtml.Node.to_markdown` replaces a call to either with one method on the parsed tree, and the conversion
runs in C rather than a Python walk over a second parser's tree:

.. code-block:: python

    # html2text
    import html2text

    html2text.html2text(text)

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

The defaults emit opinionated GitHub-Flavored Markdown, and keyword options cover the configuration surface of both
libraries with one name per concept. The markdownify options map as:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - markdownify
      - turbohtml ``to_markdown(...)``
    - - ``heading_style`` (``atx``/``atx_closed``/``underlined``)
      - ``heading_style`` (``"atx"``/``"atx_closed"``/``"setext"``)
    - - ``bullets``
      - ``bullets``
    - - ``strong_em_symbol``
      - ``strong`` and ``emphasis`` (independent, so a superset)
    - - ``sub_symbol``, ``sup_symbol``
      - ``sub_symbol``, ``sup_symbol``
    - - ``escape_asterisks``, ``escape_underscores``
      - ``escape_asterisks``, ``escape_underscores``
    - - ``escape_misc``
      - ``escape_mode="all"``
    - - ``autolinks``
      - ``autolink``
    - - ``default_title``
      - ``link_title``
    - - ``table_infer_header``
      - ``table_header="first"`` (the default) vs ``"none"``
    - - ``newline_style`` (``spaces``/``backslash``)
      - ``line_break`` (``"spaces"``/``"backslash"``)
    - - ``strip_document``
      - ``document_strip`` (``"strip"``/``"lstrip"``/``"rstrip"``/``"none"``)
    - - ``code_language``
      - ``code_language``

The html2text options map as:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - html2text
      - turbohtml ``to_markdown(...)``
    - - ``ul_item_mark``
      - ``bullets``
    - - ``emphasis_mark``, ``strong_mark``
      - ``emphasis``, ``strong``
    - - ``ignore_emphasis``
      - ``ignore_emphasis``
    - - ``ignore_links``
      - ``ignore_links``
    - - ``skip_internal_links``
      - ``skip_internal_links``
    - - ``inline_links``
      - ``link_style`` (``"inline"``/``"reference"``)
    - - ``ignore_images``, ``images_to_alt``, ``images_as_html``, ``images_with_size``
      - ``image_mode`` (``"markdown"``/``"alt"``/``"ignore"``/``"html"``)
    - - ``default_image_alt``
      - ``default_image_alt``
    - - ``ignore_tables``, ``bypass_tables``
      - ``table_mode`` (``"markdown"``/``"strip"``/``"html"``)
    - - ``pad_tables``
      - ``pad_tables``
    - - ``body_width``, ``wrap_list_items``, ``wrap_links``
      - ``wrap_width``, ``wrap_list_items``, ``wrap_links``
    - - ``unicode_snob`` (and the ``UNIFIABLE`` table)
      - ``transliterate``
    - - ``mark_code``
      - ``mark_code``
    - - ``backquote_code_style``
      - ``code_block_style`` (``"fenced"``/``"indented"``)
    - - ``single_line_break``
      - ``block_spacing="single"``
    - - ``baseurl``
      - ``base_url``
    - - ``open_quote``, ``close_quote``
      - ``quote_open``, ``quote_close``
    - - ``escape_snob``
      - ``escape_mode="all"``
    - - ``google_doc``
      - ``google_doc``
    - - ``google_list_indent``
      - ``google_list_indent``
    - - ``hide_strikethrough``
      - ``hide_strikethrough``

``google_doc=True`` reads the inline-CSS styling a Google Docs HTML export carries: a ``font-weight`` of ``bold`` or
``700``--``900`` becomes ``strong``, ``font-style:italic`` becomes ``emphasis``, a ``Courier New``/``Consolas``
``font-family`` becomes an inline code span, ``list-style-type`` picks the list marker, and each ``google_list_indent``
pixels of ``margin-left`` add one list-nesting level. With ``hide_strikethrough=True`` a
``text-decoration:line-through`` drops the struck text.

.. testcode::

    export = '<p><span style="font-weight:700">Quarterly</span> revenue</p>'
    print(parse(export).to_markdown(google_doc=True))

.. testoutput::

    **Quarterly** revenue

Pitfalls
========

- The bold and italic markers are independent (``strong`` and ``emphasis``), where markdownify derives both from one
  ``strong_em_symbol``; set both to reproduce its behavior.
- ``to_markdown`` is a method on any node, so convert a subtree by calling it on the element you selected
  (``doc.find("article").to_markdown()``) instead of slicing the HTML string first.
- A few niche knobs are intentionally dropped: the parser-selection options (markdownify's ``bs4_options``) and the
  per-call tag-handler callbacks, since turbohtml always runs the WHATWG algorithm and the walk holds no per-call state.
  ``base_url`` does simple prefixing rather than full RFC-3986 URL resolution.
- Layout-aware plain text (the ``inscriptis`` role, ``to_text(layout=...)``) is a separate method; for the unstructured
  concatenation read :attr:`~turbohtml.Node.text`.

*****************
 From inscriptis
*****************

`inscriptis <https://github.com/weblyzard/inscriptis>`_ renders HTML to *layout-aware* plain text: it keeps the visual
structure, most visibly by laying tables out as aligned columns. :meth:`~turbohtml.Node.to_text` is the same idea in C,
replacing ``get_text``:

.. code-block:: python

    # inscriptis
    from inscriptis import get_text

    get_text(html)

    # turbohtml
    import turbohtml

    turbohtml.parse(html).to_text()

.. testcode::

    html = "<h1>Sales</h1><table><tr><th>Region</th><th>Total</th></tr><tr><td>North</td><td>120</td></tr></table>"
    print(parse(html).to_text())

.. testoutput::

    Sales

    Region  Total
    North   120

The ``ParserConfig`` options map as:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - inscriptis
      - turbohtml ``to_text(...)``
    - - ``display_links``
      - ``links`` (``"none"``/``"inline"``/``"footnote"``)
    - - ``display_images``
      - ``images``
    - - ``table_cell_separator``
      - ``table_cell_separator``
    - - the ``strict`` / ``relaxed`` CSS profile
      - ``layout`` (``"strict"``/``"extended"``)
    - - the list bullet
      - ``bullet``
    - - (no equivalent)
      - ``width`` adds word wrapping, which inscriptis leaves to the caller

inscriptis can also tag the rendered text with labeled spans through ``get_annotated_text`` and an ``annotation_rules``
mapping. :meth:`~turbohtml.Node.to_annotated_text` is the same call: it returns the rendered text together with a list
of ``(start, end, label)`` triples over its code-point offsets, taking every ``to_text`` option as well.

.. code-block:: python

    # inscriptis
    from inscriptis import get_annotated_text, ParserConfig

    rules = {"h1": ["heading"], "b": ["emphasis"]}
    get_annotated_text(html, ParserConfig(annotation_rules=rules))

    # turbohtml
    turbohtml.parse(html).to_annotated_text(rules)

.. testcode::

    text, labels = parse("<h1>Title</h1><p>Some <b>bold</b> words.</p>").to_annotated_text(
        {"h1": ["heading"], "b": ["emphasis"]}
    )
    print(text)
    print([(label, text[start:end]) for start, end, label in labels])

.. testoutput::

    Title

    Some bold words.
    [('heading', 'Title'), ('emphasis', 'bold')]

Rule keys follow inscriptis: a bare tag (``"h1"``), a ``tag#attr`` to require an attribute, a ``tag#attr=value`` to
match one whitespace-separated token of it, and the tag-less ``#attr`` / ``#attr=value`` forms to match across any tag.
The value is the list of labels to attach.

Pitfalls
========

- Links are hidden by default (matching inscriptis); pass ``links="inline"`` for ``text (url)`` or ``links="footnote"``
  for numbered references collected at the end.
- ``to_text`` renders structure, not styling: there is no bold or color, and headings are plain text. For the raw
  concatenation with no layout at all, read :attr:`~turbohtml.Node.text`.
- Annotation offsets count code points into the returned string; a table cell is labeled at its position in the laid-out
  grid, so the span covers the cell's column-aligned text rather than the source order.
