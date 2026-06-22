.. _migration-lxml:

###########
 From lxml
###########

.. image:: https://static.pepy.tech/badge/lxml
    :alt: lxml downloads
    :target: https://pepy.tech/project/lxml

`lxml <https://lxml.de>`_ is the libxml2/libxslt binding that most Python HTML and XML processing has been built on:
``lxml.html`` parses documents into ElementTree-style elements with ``.text``/``.tail`` strings, and the wider stack
adds XPath, XSLT, and schema validation.

***************
 Why turbohtml
***************

:func:`turbohtml.parse` builds the WHATWG document tree that libxml2's HTML parser does not, returns a fully type
annotated :class:`~turbohtml.Document`, and folds XPath, CSS, and the ``find``/``find_all`` grammar into one node API
instead of separate ``findall``/``xpath``/``cssselect`` entry points. It parses two to four times faster than lxml while
matching a browser on malformed input:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - parse
      - turbohtml
      - lxml
    - - wpt page (4 kB)
      - 10.9 µs
      - 27.2 µs
    - - wpt page (92 kB)
      - 269 µs
      - 633 µs
    - - whatwg spec (235 kB)
      - 510 µs
      - 1.24 ms
    - - ecmascript spec (3 MB)
      - 4.48 ms
      - 17.5 ms

The :doc:`/development/performance` page also benchmarks turbohtml's serializer, builder, editor, CSS, and XPath 1.0
engine against lxml directly.

*************
 The renames
*************

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
      - :attr:`~turbohtml.Element.tag` (same)
    - - ``el.get("x")``, ``el.attrib``, ``el.set("x", "v")``
      - :attr:`~turbohtml.Element.attrs` (``attrs.get("x")``, ``attrs["x"] = "v"``)
    - - ``el.text``, ``el.tail``
      - child :class:`~turbohtml.Text` nodes; iterate :attr:`~turbohtml.Node.children`
    - - ``el.text_content()``
      - :attr:`~turbohtml.Node.text`
    - - ``el.getparent()``, ``el.getnext()``, ``el.getprevious()``
      - :attr:`~turbohtml.Node.parent`, :attr:`~turbohtml.Node.next_sibling`, :attr:`~turbohtml.Node.previous_sibling`
    - - ``list(el)``, ``el.iterdescendants()``, ``el.iterancestors()``
      - :attr:`~turbohtml.Node.children`, :attr:`~turbohtml.Node.descendants`, :attr:`~turbohtml.Node.ancestors`
    - - ``el.findall(".//a")``, ``el.xpath("//a[@href]")``
      - :meth:`~turbohtml.Node.find_all`, :meth:`~turbohtml.Node.xpath`
    - - ``el.cssselect("div a")``
      - :meth:`~turbohtml.Node.select`
    - - ``lxml.html.Element("div")``, ``etree.SubElement(p, "div")``
      - :class:`~turbohtml.Element`, :meth:`p.append(Element("div")) <turbohtml.Element.append>`
    - - ``el.drop_tag()``, ``el.drop_tree()``
      - :meth:`~turbohtml.Node.unwrap`, :meth:`~turbohtml.Node.decompose`
    - - ``el.sourceline``
      - :attr:`~turbohtml.Node.source_line` (1-based, like lxml; plus :attr:`~turbohtml.Node.source_col`)
    - - ``el.iterlinks()``
      - :meth:`~turbohtml.Node.links`
    - - ``el.make_links_absolute(base)``, ``el.rewrite_links(fn)``
      - :meth:`~turbohtml.Node.resolve_links`, :meth:`~turbohtml.Node.rewrite_links`
    - - ``lxml.html.tostring(el)``
      - :attr:`~turbohtml.Node.html`

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
- The wider libxml2 toolchain is a deliberate clean-break scope cut: XSLT, DTD/RelaxNG/XML-Schema validation, and C14N
  have no turbohtml equivalent, and XPath is the 1.0 engine only (XPath 2.0+ and XQuery are out of scope).
