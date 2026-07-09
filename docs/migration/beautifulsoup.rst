####################
 From BeautifulSoup
####################

.. package-meta:: beautifulsoup4

`BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/bs4/doc/>`_ is the long-standing convenience layer over a
choice of HTML parsers (``html.parser``, ``lxml``, or ``html5lib``): you pick a backend, then navigate and search the
resulting soup with a large, alias-rich API. It also parses XML through the ``lxml-xml`` backend, decodes unknown byte
streams with ``UnicodeDammit``, and matches CSS selectors through its ``soupsieve`` dependency. Its reach and forgiving
API made it the default scraping tool for a generation of Python code, from one-off screen scrapes to production
pipelines.

turbohtml covers the HTML side of that ground with one engine. :func:`turbohtml.parse` runs the WHATWG algorithm in C,
returns a fully type annotated :class:`~turbohtml.Document`, and exposes a single ``find``/``find_all``/``select``
grammar plus XPath. It shares the most surface with turbohtml of any library here, so this is the deepest migration
section.

****************************
 turbohtml vs BeautifulSoup
****************************

.. list-table::
    :header-rows: 1
    :widths: 18 41 41

    - - Dimension
      - turbohtml
      - BeautifulSoup
    - - Scope
      - WHATWG HTML: parse, query, mutate, and serialize in one C engine
      - HTML and XML navigation layer over a pluggable third-party parser
    - - Feature breadth
      - ``find`` grammar with :class:`~turbohtml.Axis`, CSS selectors, XPath, source positions, linkify, WHATWG
        serialization formatters
      - ``find`` grammar, CSS via ``soupsieve``, XML mode, ``UnicodeDammit`` encoding guessing, output-formatter
        registry
    - - Performance
      - C core; one to three orders of magnitude faster than ``bs4`` over ``html.parser`` across parse, query, and
        serialize
      - Pure-Python navigation; speed tracks the chosen backend and is slowest on ``html.parser``
    - - Typing
      - Ships ``py.typed``; every public method annotated
      - No inline types; relies on the third-party ``types-beautifulsoup4`` stubs
    - - Dependencies
      - Zero runtime dependencies (self-contained C extension)
      - Requires ``soupsieve``; ``lxml`` or ``html5lib`` optional for those backends
    - - Maintenance
      - Actively developed, single-engine
      - Mature, widely deployed, long release history

Feature overlap
===============

These port one-to-one; the calls differ only in name (see the mapping table under `How to migrate`_):

- Parsing markup into a navigable tree.
- ``find`` / ``find_all`` by tag, attribute, ``class_``, and text.
- CSS selectors via ``select`` / ``select_one``.
- Directional navigation (parents, siblings, next/previous elements) — turbohtml folds these into ``find`` with an
  :class:`~turbohtml.Axis`.
- Tree mutation: ``decompose``, ``extract``, ``unwrap``, ``wrap``, ``insert_before``, ``insert_after``,
  ``replace_with``.
- Text access: ``get_text`` maps to :attr:`~turbohtml.Node.text`, :attr:`~turbohtml.Node.strings`, and
  :attr:`~turbohtml.Node.stripped_strings`.
- Pretty-printing via :meth:`~turbohtml.Node.serialize` with ``Html(layout=Indent(2))`` for ``prettify()``.
- Source positions: ``sourceline`` / ``sourcepos`` map to :attr:`~turbohtml.Node.source_line` /
  :attr:`~turbohtml.Node.source_col`.

What turbohtml adds
===================

- **XPath.** :meth:`~turbohtml.Node.xpath` and :meth:`~turbohtml.Node.xpath_iter` evaluate expressions with namespaces,
  variables, and extension functions. BeautifulSoup has no XPath support at all.
- **A C engine.** Parsing, querying, text collection, and serialization all run in C, so migrating off ``html.parser``
  is a large speedup with no backend to install.
- **WHATWG-conformant serialization.** :class:`~turbohtml.Formatter` selection controls entities, and output matches the
  spec by default; ``Formatter.NAMED_ENTITIES`` reproduces ``bs4``'s ``html`` formatter when you need it.
- **Post-parse pruning.** :meth:`~turbohtml.Node.prune` trims a fully parsed tree to a CSS selector in one C pass.
- **Zero runtime dependencies and full typing.** No ``soupsieve`` install, and every public API is annotated.

What BeautifulSoup has that turbohtml does not
==============================================

- **XML parsing.** ``BeautifulSoup(markup, "lxml-xml")`` parses arbitrary XML. turbohtml runs the WHATWG *HTML*
  algorithm only; there is no XML tree-builder. No equivalent — keep ``lxml`` for XML documents.
- **Pluggable parser backends.** ``bs4`` lets you swap ``html.parser``, ``lxml``, and ``html5lib``. turbohtml always
  runs its own WHATWG parser; this is a deliberate clean-break omission, not a gap you work around.
- **Statistical encoding detection.** ``UnicodeDammit`` (and ``chardet`` / ``charset-normalizer``) guess an encoding
  from byte frequency when there is no BOM or ``<meta charset>``. turbohtml sniffs only what the WHATWG algorithm reads,
  then falls back to ``windows-1252``. Workaround: detect with ``charset-normalizer`` first and hand turbohtml the
  decoded ``str`` (or bytes with an explicit ``encoding=``).
- **A named output-formatter registry.** ``bs4`` registers custom formatters by name. Workaround: pass a
  :class:`~turbohtml.Formatter` per :meth:`~turbohtml.Node.serialize` call.

Performance
===========

turbohtml parses, queries, and serializes one to three orders of magnitude faster than BeautifulSoup over
``html.parser``; even the closest-margin operations — text filtering (``find(text=...)`` against
``find_all(string=...)``), walking the tree (:attr:`~turbohtml.Node.descendants` against ``soup.descendants``), and
reading text (:attr:`~turbohtml.Node.text` against ``soup.get_text()``) — still run a few times faster:

.. bench-table::
    :file: bench/beautifulsoup.json

The :doc:`/development/performance` page benchmarks the build and edit paths against BeautifulSoup too.

****************
 How to migrate
****************

Swap the import and the constructor. There is no parser name to pass, since turbohtml always runs the WHATWG algorithm:

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

    doc = parse(b'<meta charset="latin1"><p>caf\xe9</p>')
    print(doc.find("p").text)
    print(doc.encoding)  # the encoding the WHATWG label latin1 names

.. testoutput::

    café
    windows-1252

API mapping
===========

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`__
      - turbohtml
    - - ``tag.name``
      - :attr:`~turbohtml.Element.tag`
    - - ``tag["class"]``, ``tag.get("x")``, ``tag.has_attr("x")``
      - :attr:`~turbohtml.Element.attrs` (``attrs["class"]``, ``attrs.get("x")``, ``"x" in attrs``)
    - - ``tag.string``, ``tag.get_text()``
      - :attr:`~turbohtml.Node.text`, :attr:`~turbohtml.Node.strings`, :attr:`~turbohtml.Node.stripped_strings`
    - - ``tag.parents``
      - :attr:`~turbohtml.Node.ancestors`
    - - ``tag.contents``, ``list(tag.children)``
      - :attr:`~turbohtml.Node.children`
    - - ``tag.next_elements``
      - :attr:`~turbohtml.Node.following`
    - - ``tag.find_parent(...)``
      - :meth:`~turbohtml.Node.find` (``axis=Axis.ANCESTORS``) or :meth:`~turbohtml.Node.closest`
    - - ``tag.find_next(...)``, ``tag.find_previous(...)``
      - :meth:`~turbohtml.Node.find` with ``axis=Axis.FOLLOWING`` / ``Axis.PRECEDING``
    - - ``tag.find_next_sibling(...)``, ``tag.find_previous_sibling(...)``
      - :meth:`~turbohtml.Node.find` with ``axis=Axis.NEXT_SIBLINGS`` / ``Axis.PREVIOUS_SIBLINGS``
    - - ``tag.find_all("a", recursive=False)``
      - :meth:`~turbohtml.Node.find_all` (``axis=Axis.CHILDREN``)
    - - ``soup.find(string=re.compile(r"\$\d+"))``, ``soup.find_all(string="Add to cart")``
      - :meth:`~turbohtml.Node.find` / :meth:`~turbohtml.Node.find_all` with ``text=``
    - - ``soup.select(".cls")``, ``soup.select_one(".cls")``
      - :meth:`~turbohtml.Node.select`, :meth:`~turbohtml.Node.select_one`
    - - (no XPath)
      - :meth:`~turbohtml.Node.xpath`, :meth:`~turbohtml.Node.xpath_iter`
    - - ``BeautifulSoup(markup, parse_only=SoupStrainer("article"))``
      - ``turbohtml.parse(markup).prune("article")`` (:meth:`~turbohtml.Node.prune`)
    - - ``tag.decompose()``, ``tag.extract()``, ``tag.unwrap()``, ``tag.wrap(...)``
      - :meth:`~turbohtml.Node.decompose`, :meth:`~turbohtml.Node.extract`, :meth:`~turbohtml.Node.unwrap`,
        :meth:`~turbohtml.Node.wrap`
    - - ``tag.insert_before(...)``, ``tag.insert_after(...)``, ``tag.replace_with(...)``
      - :meth:`~turbohtml.Node.insert_before`, :meth:`~turbohtml.Node.insert_after`,
        :meth:`~turbohtml.Node.replace_with`
    - - ``soup.new_tag("div")``, ``soup.new_string("hi")``
      - :class:`~turbohtml.Element`, :class:`~turbohtml.Text`
    - - ``tag.prettify()``
      - :meth:`~turbohtml.Node.serialize` (``Html(layout=Indent(2))``)
    - - ``tag.smooth()``
      - :meth:`~turbohtml.Element.normalize`
    - - ``tag.sourceline``, ``tag.sourcepos``
      - :attr:`~turbohtml.Node.source_line`, :attr:`~turbohtml.Node.source_col`

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

**********************
 Gotchas and pitfalls
**********************

- **Indexing reaches children, not attributes.** ``node[i]`` indexes child nodes, so attributes are reached only through
  ``.attrs``, never ``node["attr"]``. Multi-valued attributes (``class``, ``rel``, ...) read back as a ``list[str]``.

  .. testcode::

      a = parse('<a class="btn lg" href="/x">go</a>').find("a")
      print(a.attrs["class"])
      print(a[0])  # indexing reaches children, never attributes

  .. testoutput::

      ['btn', 'lg']
      Text('go')

- **No ``.string`` shortcut and no ``text``/``tail`` split.** Text is real child nodes (the WHATWG DOM shape), so there
  is no ``.string`` and no `lxml <https://lxml.de>`_-style ``text``/``tail``; iterate the children or read
  :attr:`~turbohtml.Node.text`:

  .. testcode::

      p = parse("<p>Hello <b>bold</b> world</p>").find("p")
      print((p.text, list(p.stripped_strings)))

  .. testoutput::

      ('Hello bold world', ['Hello', 'bold', 'world'])

- **``text=`` filters elements, not ``NavigableString``.** ``bs4``'s ``find(string=...)`` returns the matching string
  node; turbohtml's ``text=`` filters *elements* by their collected subtree text, so it composes with tag and attribute
  filters. A plain string matches the full text; use a regex to search within:

  .. testcode::

      import re

      doc = parse("<ul><li>Buy now</li><li>Later</li></ul>")
      print(doc.find("li", text="Buy now").text)
      print([li.text for li in doc.find_all("li", text=re.compile(r"now"))])

  .. testoutput::

      Buy now
      ['Buy now']

- **Serialization is WHATWG-conformant by default.** Output differs from ``bs4``'s ``html`` formatter on named entities,
  attribute order, and ``<br>`` versus ``<br/>``. Pick ``Formatter.NAMED_ENTITIES`` to approximate ``bs4``:

  .. testcode::

      from turbohtml import Formatter, Html

      node = parse("<p>café &amp; co</p>").find("p")
      print(node.html)
      print(node.serialize(Html(formatter=Formatter.NAMED_ENTITIES)))

  .. testoutput::

      <p>café &amp; co</p>
      <p>caf&eacute; &amp; co</p>

- **Encoding sniffing stops at the markup.** ``parse`` runs the WHATWG algorithm on bytes — BOM, then a ``<meta
  charset>`` prescan, then a ``windows-1252`` fallback — which covers what ``UnicodeDammit`` reads from the markup but
  does not guess from byte frequency. A stream with no BOM and no declaration lands on ``windows-1252`` where
  ``charset-normalizer`` might name, say, ``koi8-r``. When there is nothing to sniff, detect first and hand turbohtml
  the decoded ``str``.
- **``==`` compares identity; structural equality is a method.** Two parses of the same markup are ``==``-unequal
  (``==`` stays node identity, so nodes remain usable as dict keys). Where ``bs4`` leaned on structural ``==``, call
  :meth:`~turbohtml.Node.equals`: ``a.equals(b)`` compares two subtrees by name, attributes, and contents, the ``bs4``
  notion of tree equality.
- **``SoupStrainer`` has no parse-time equivalent.** turbohtml always runs the full WHATWG algorithm, then
  :meth:`~turbohtml.Node.prune` trims the parsed tree to a CSS selector in one C pass, so a large document still yields
  a small tree — but the whole document is parsed first.
