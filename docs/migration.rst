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
      - ``node.serialize(indent=2)``
    - - ``tag.smooth()``
      - ``element.normalize()``

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

In place of subclassing :class:`python:html.parser.HTMLParser` with ``handle_starttag`` and ``handle_data`` callbacks,
take the token stream from :func:`turbohtml.tokenize` (or :meth:`turbohtml.Tokenizer.feed` for incremental input), or
skip tokens entirely and :func:`turbohtml.parse` straight to a tree. Unlike ``html.parser``, both are WHATWG-conformant.
The :doc:`how-to` guide has a worked port.

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
str]``), and a callback returns it to keep the link or ``None`` to leave the text bare. Porting a callback means
dropping the ``new`` argument and reading fields instead of tuple keys:

.. testcode::

    from turbohtml.linkify import linkify, Link

    def shorten(link: Link) -> Link | None:
        link.text = link.url.removeprefix("https://").removeprefix("http://")
        return link

    print(linkify("read https://example.com/page", callbacks=[shorten]))

.. testoutput::

    read <a href="https://example.com/page">example.com/page</a>

Two behaviors differ from bleach, both deliberate. bleach also ran the callbacks over the links already present in the
input; turbohtml leaves an existing ``<a>`` untouched, so linkifying is idempotent and never rewrites a link an author
wrote, which is why the callback drops bleach's ``new`` flag. A bare domain such as ``example.com`` links only when its
last label is a current IANA TLD, from a table you can regenerate, where bleach shipped a frozen list. The scan for link
candidates runs in C, so linkifying a page is faster than bleach's html5lib-based pass.

********************
 From linkify-it-py
********************

`linkify-it-py <https://github.com/tsutsu3/linkify-it-py>`_ scans plain text and returns the link spans it finds;
turning them into ``<a>`` tags, and skipping text that is already markup, is left to the caller. turbohtml does both.
Where linkify-it-py hands back ``Match`` objects with ``url`` and offset fields, turbohtml returns the rewritten HTML:

.. code-block:: python

    # linkify-it-py
    from linkify_it import LinkifyIt

    matches = LinkifyIt().match("see https://example.com")
    # [Match(url="https://example.com", ...)] or None, and you build the <a> yourself

.. testcode::

    from turbohtml.linkify import linkify

    print(linkify("see https://example.com"))

.. testoutput::

    see <a href="https://example.com" rel="nofollow">https://example.com</a>

Because turbohtml parses the input as HTML, it leaves alone a URL already inside an ``<a>`` or a ``<script>`` that
linkify-it-py, working on the raw string, would match again. linkify-it-py is configurable down to custom schemes and
fuzzy IP matching; turbohtml covers the common web, ``mailto:``, and bare-domain cases and trades that breadth for being
HTML-aware and several times faster.
