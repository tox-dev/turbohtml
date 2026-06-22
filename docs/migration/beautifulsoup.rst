####################
 From BeautifulSoup
####################

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

********************
 Encoding detection
********************

``parse`` runs the WHATWG sniffing algorithm on bytes: a leading BOM, then a ``<meta charset>`` prescan, then a
``windows-1252`` fallback. That covers what ``UnicodeDammit`` reads from the markup, and it stops there. turbohtml does
not guess an encoding from the byte distribution. ``UnicodeDammit``'s optional statistical pass and the dedicated
detectors (`charset-normalizer <https://github.com/jawah/charset_normalizer>`_, `chardet
<https://github.com/chardet/chardet>`_, ``cchardet``) read byte frequency, so a markup-less stream, or a document with
no BOM and no declaration, lands on ``windows-1252`` here where they would name, say, ``koi8-r``. When there is nothing
to sniff, detect the encoding with ``charset-normalizer`` first and hand turbohtml the decoded ``str`` (or the bytes
with an explicit ``encoding=``).

*************
 The renames
*************

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
    - - ``BeautifulSoup(markup, parse_only=SoupStrainer("article"))``
      - ``turbohtml.parse(markup).prune("article")``
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

***********
 Searching
***********

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

*********************
 Attributes and text
*********************

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

********
 Output
********

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

**********
 Pitfalls
**********

- ``node[i]`` indexes children; attributes are reached through ``.attrs``, never ``node["attr"]``.
- Text is real child nodes, so there is no ``.string`` shortcut and no ``text``/``tail``; iterate the children.
- Default output is WHATWG-conformant; pick ``Formatter.NAMED_ENTITIES`` to come close to ``bs4``'s ``html`` formatter.
- ``==`` compares identity, so two trees with the same markup are unequal. Where ``bs4`` code leaned on ``==`` between
  trees, compare serializations (``a.html == b.html``) or walk the nodes.
- ``SoupStrainer`` filtered the tree *during* parsing; turbohtml always runs the full WHATWG algorithm, then
  :meth:`~turbohtml.Node.prune` trims the parsed tree to a CSS selector in one C pass, so a large document still yields
  a small tree.
- A couple of bs4 entry points are deliberate clean-break omissions: the choice of parser backend (turbohtml always runs
  the WHATWG algorithm) and registering a named output formatter. Pick a :class:`~turbohtml.Formatter` per
  :meth:`~turbohtml.Node.serialize` call instead.
