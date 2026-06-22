.. _migration-lxml:

###########
 From lxml
###########

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

**********
 Pitfalls
**********

- No ``text``/``tail``. A node's children are its text runs and elements interleaved; read :attr:`~turbohtml.Node.text`
  for the concatenation.
- lxml parses with libxml2, which is not WHATWG-conformant, so malformed input lands in a different tree than the one
  turbohtml (and a browser) builds.
- For a document that arrives in pieces, ``etree.iterparse`` is replaced by :class:`turbohtml.IncrementalParser`: feed
  ``str`` or ``bytes`` chunks with ``feed`` and call ``close`` for the finished :class:`~turbohtml.Document`. The parser
  never holds the whole source at once, so you can parse a stream larger than the source buffer you would otherwise
  materialize for :func:`turbohtml.parse`.

******************************
 Not yet ported / limitations
******************************

XPath 1.0 (:meth:`~turbohtml.Node.xpath`), CSS :meth:`~turbohtml.Node.select`, the ``find``/``find_all`` filter grammar,
:attr:`~turbohtml.Node.source_line`/:attr:`~turbohtml.Node.source_col`, and :meth:`~turbohtml.Node.links`/
:meth:`~turbohtml.Node.resolve_links` all ship, but the wider libxml2 toolchain lxml exposes is a deliberate clean-break
scope cut:

- XSLT, DTD/RelaxNG/XML-Schema validation, and C14N have no turbohtml equivalent.
- XPath is the 1.0 engine only; XPath 2.0+ and XQuery are out of scope.
- The ``.text``/``.tail`` string model is replaced by real :class:`~turbohtml.Text` child nodes, not reproduced.
