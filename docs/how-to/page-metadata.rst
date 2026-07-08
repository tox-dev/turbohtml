#############################
 Read a page's head metadata
#############################

A page's ``<head>`` holds the facts a link preview or an index wants: the title, the description, the canonical URL, and
the OpenGraph card. Read them off a parsed document with :meth:`~turbohtml.Node.select_one` and
:meth:`~turbohtml.Element.attr`, and let :meth:`~turbohtml.Document.structured_data` gather the OpenGraph block.

********************
 Read the head tags
********************

:meth:`~turbohtml.Node.select_one` finds each element by a CSS selector, and :meth:`~turbohtml.Element.attr` reads an
attribute (returning ``None`` when it is absent, so a missing tag never raises):

.. testcode::

    import turbohtml

    doc = turbohtml.parse(
        "<html><head><title>Widget — Shop</title>"
        '<meta name="description" content="Buy the widget, ships today.">'
        '<link rel="canonical" href="https://shop.example/widget">'
        "</head><body><h1>Widget</h1></body></html>"
    )
    print(doc.select_one("title").text)
    print(doc.select_one('meta[name="description"]').attr("content"))
    print(doc.select_one('link[rel="canonical"]').attr("href"))

.. testoutput::

    Widget — Shop
    Buy the widget, ships today.
    https://shop.example/widget

*************************
 Read the OpenGraph card
*************************

The OpenGraph and Twitter-card tags come off :meth:`~turbohtml.Document.structured_data`, which reads them into a dict
in one walk rather than a selector per property, the ``metadata_parser`` and ``opengraph`` use case:

.. testcode::

    doc = turbohtml.parse(
        '<head><meta property="og:title" content="Widget">'
        '<meta property="og:image" content="https://shop.example/w.png">'
        '<meta name="twitter:card" content="summary"></head>'
    )
    card = doc.structured_data().opengraph
    print(card["og:title"], card["og:image"])
    print(card["twitter:card"])

.. testoutput::

    Widget https://shop.example/w.png
    summary

Read the title, the description, the canonical link, and the card in one parse, no per-field library. For the full set
of embedded formats -- JSON-LD, Microdata, RDFa -- see :doc:`structured-data`.
