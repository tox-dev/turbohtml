#######################
 Parse XML into a tree
#######################

When your input is XML rather than HTML, use :func:`turbohtml.parse_xml`. It parses under XML 1.0 well-formedness
instead of the WHATWG tree builder, so names stay case-sensitive, ``<x/>`` self-closes any element, and there is no HTML
recovery -- a malformed document raises. The result is the same navigable :class:`~turbohtml.Document` the HTML path
returns, so you query, edit, and serialize it through the one node API.

******************************
 Parse a well-formed document
******************************

Hand :func:`turbohtml.parse_xml` a string of XML. The document's children are its prolog nodes (an optional doctype,
comments, processing instructions) followed by the single root element:

.. testcode::

    import turbohtml

    doc = turbohtml.parse_xml("<catalog><book id='b1'><title>One</title></book></catalog>")
    catalog = doc.children[0]
    book = catalog.children[0]
    print(catalog.tag, book.attrs["id"])
    print(book.children[0].tag, book.children[0].text)

.. testoutput::

    catalog b1
    title One

XML elements carry no HTML tag identity, so navigate the tree structurally through :attr:`~turbohtml.Node.children`,
:attr:`~turbohtml.Node.parent`, and the sibling links, and read each :attr:`~turbohtml.Element.tag` verbatim.

*********************************
 Read namespaces, CDATA, and PIs
*********************************

turbohtml keeps qualified names verbatim -- an element declared under a prefix keeps its ``prefix:local`` tag, and every
``xmlns``/``xmlns:prefix`` declaration stays as an ordinary attribute. A CDATA section becomes a
:class:`~turbohtml.CData` node and a processing instruction a :class:`~turbohtml.ProcessingInstruction` with a
``target`` and ``data``:

.. testcode::

    doc = turbohtml.parse_xml(
        '<feed xmlns:dc="urn:dc">'
        "<?render mode=fast?>"
        "<dc:title>News</dc:title>"
        "<summary><![CDATA[<b>raw</b> & co]]></summary>"
        "</feed>"
    )
    feed = doc.children[0]
    print(feed.attrs["xmlns:dc"])
    print(feed.children[0].target, feed.children[0].data)
    print(feed.children[1].tag)
    print(feed.children[2].children[0].data)

.. testoutput::

    urn:dc
    render mode=fast
    dc:title
    <b>raw</b> & co

Only the five predefined entities (``&amp;``, ``&lt;``, ``&gt;``, ``&quot;``, ``&apos;``) and numeric character
references such as ``&#233;`` resolve; a document that defines its own entities through a DTD is outside this mode.

*******************************
 Catch a well-formedness error
*******************************

Ill-formed XML raises :exc:`~turbohtml.HTMLParseError` at the first violation rather than recovering. Its ``error``
attribute is a :class:`~turbohtml.ParseError` carrying the ``code`` and the source position, so a linter or ingest
pipeline can report exactly what and where:

.. testcode::

    try:
        turbohtml.parse_xml("<a><b></a>")
    except turbohtml.HTMLParseError as exception:
        print(exception.error.code, exception.error.line, exception.error.col)

.. testoutput::

    xml-mismatched-tag 1 8

The same exception surfaces an unclosed tag, an undeclared namespace prefix, an undefined entity, a duplicate attribute,
content outside the root element, or a Namespaces in XML 1.0 violation -- rebinding the reserved ``xml`` or ``xmlns``
prefix, a colon in a processing-instruction target, or two attributes that resolve to the same expanded name.
