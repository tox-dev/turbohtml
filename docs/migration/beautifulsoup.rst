####################
 From BeautifulSoup
####################

.. image:: https://static.pepy.tech/badge/beautifulsoup4/month
    :alt: beautifulsoup4 monthly downloads
    :target: https://pepy.tech/project/beautifulsoup4

`BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/bs4/doc/>`_ is the long-standing convenience layer over a
choice of HTML parsers (``html.parser``, ``lxml``, or ``html5lib``): you pick a backend, then navigate and search the
resulting soup with a large, alias-rich API. It shares the most surface with turbohtml, so this is the deepest section.

***************
 Why turbohtml
***************

:func:`turbohtml.parse` returns a fully type annotated :class:`~turbohtml.Document` with no parser backend to choose,
since it always runs the WHATWG algorithm in C. The search surface is one ``find``/``find_all``/``select`` grammar with
:class:`~turbohtml.Axis` directions instead of a dozen directional finders. And it parses, queries, and serializes one
to two orders of magnitude faster than BeautifulSoup over ``html.parser``:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - input
      - turbohtml
      - BeautifulSoup
      - speed-up
    - - parse wpt page (4 kB)
      - 11.4 µs
      - 438 µs
      - 38.5x
    - - parse wpt page (92 kB)
      - 272 µs
      - 15.3 ms
      - 56.2x
    - - select ``div a[href]`` (4 kB)
      - 0.04 µs
      - 41.8 µs
      - 1010.3x
    - - serialize wpt page (92 kB)
      - 105 µs
      - 5.95 ms
      - 56.8x

The :doc:`/development/performance` page benchmarks the build and edit paths against BeautifulSoup too.

Filtering elements by text through :meth:`~turbohtml.Node.find` / :meth:`~turbohtml.Node.find_all` (``text=``) gathers
each candidate's subtree text in C and matches once, where ``bs4``'s ``find_all(string=...)`` runs the predicate in
Python mid-walk. The :doc:`/development/performance` query suite races the two over the wpt pages:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - find ``text=`` regex
      - turbohtml
      - BeautifulSoup
      - speed-up
    - - wpt page (4 kB)
      - 9.3 µs
      - 19.7 µs
      - 2.1x
    - - wpt page (9.6 kB)
      - 13.9 µs
      - 38.2 µs
      - 2.7x
    - - wpt page (92 kB)
      - 741 µs
      - 989 µs
      - 1.3x

Walking the whole tree (:attr:`~turbohtml.Node.descendants` against ``soup.descendants``) and reading its text
(:attr:`~turbohtml.Node.text` against ``soup.get_text()``) stay ahead too; both libraries parse once outside the timed
region. The descendant walk yields comparable node counts on both sides, so the gap is narrower, while ``get_text``
concatenates in C where ``bs4`` joins Python strings node by node:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - navigate (walk descendants)
      - turbohtml
      - BeautifulSoup
      - speed-up
    - - wpt page (4 kB)
      - 1.3 µs
      - 2.7 µs
      - 2.1x
    - - wpt page (9.6 kB)
      - 2.3 µs
      - 4.8 µs
      - 2.1x
    - - wpt page (92 kB)
      - 65.2 µs
      - 123.3 µs
      - 1.9x

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - text (whole-document ``get_text``)
      - turbohtml
      - BeautifulSoup
      - speed-up
    - - wpt page (4 kB)
      - 0.8 µs
      - 6.7 µs
      - 8.3x
    - - wpt page (9.6 kB)
      - 1.1 µs
      - 13.7 µs
      - 12.9x
    - - wpt page (92 kB)
      - 38.0 µs
      - 372.4 µs
      - 9.8x

*********
 Parsing
*********

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
      - :meth:`~turbohtml.Node.serialize` (``layout=Indent(2)``)
    - - ``tag.smooth()``
      - :meth:`~turbohtml.Element.normalize`
    - - ``tag.sourceline``, ``tag.sourcepos``
      - :attr:`~turbohtml.Node.source_line`, :attr:`~turbohtml.Node.source_col`

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

``text`` replaces ``bs4``'s ``find(string=...)`` search. The one shift: ``bs4`` returns the matching
``NavigableString``, while ``text`` filters *elements* by their collected text (the whole subtree, as
:attr:`~turbohtml.Node.text` returns), so it composes with the tag and attribute filters and a plain string is the full
text rather than a substring (use a regex to search within):

.. testcode::

    import re

    doc = parse("<ul><li>Buy now</li><li>Later</li></ul>")
    print(doc.find("li", text="Buy now").text)
    print([li.text for li in doc.find_all("li", text=re.compile(r"now"))])

.. testoutput::

    Buy now
    ['Buy now']

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
