################################
 From extruct / metadata_parser
################################

.. image:: https://static.pepy.tech/badge/extruct/month
    :alt: extruct monthly downloads
    :target: https://pepy.tech/project/extruct

`extruct <https://github.com/scrapinghub/extruct>`_ and `metadata_parser <https://github.com/jvanasco/metadata_parser>`_
pull the machine-readable metadata a page embeds: JSON-LD, Microdata, and the OpenGraph/Twitter card tags. ``extruct``
builds an lxml tree and runs a separate extractor per syntax you list; ``metadata_parser`` focuses on the social-card
meta tags and exposes them as a flat mapping.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Document.structured_data` pulls every supported format in one walk on the parsed document, so there is
no extractor object to build or ``syntaxes`` list to configure, and the per-format helpers
:meth:`~turbohtml.Document.json_ld`, :meth:`~turbohtml.Document.opengraph`, and :meth:`~turbohtml.Document.microdata`
return just one format each. The locating runs in the C core under the per-tree critical section; only JSON-LD parsing
stays in Python (the standard library :mod:`json`), and a block that is not valid JSON is skipped rather than raising --
the robust default ``extruct`` also takes. The combined result is a frozen, fully typed
:class:`~turbohtml.StructuredData` record whose five fields you read by attribute, so reading it never depends on which
extractors you happened to enable:

.. testcode::

    from turbohtml import parse

    doc = parse(
        '<head><meta property="og:title" content="Widget"></head>'
        '<body><script type="application/ld+json">{"@type": "Product"}</script>'
        '<div itemscope itemtype="https://schema.org/Offer"><span itemprop="price">9.99</span></div></body>'
    )
    data = doc.structured_data()
    print(data.json_ld)
    print(data.opengraph)
    offer = data.microdata[0]
    print(offer.type, offer.properties)

.. testoutput::

    [{'@type': 'Product'}]
    {'og:title': 'Widget'}
    https://schema.org/Offer {'price': ['9.99']}

Both libraries start from the raw HTML string, so each parses before it extracts: ``extruct`` builds an lxml tree and
runs a separate extractor per syntax, where :meth:`~turbohtml.Document.structured_data` parses to the WHATWG tree and
gathers every format in one C walk. On a product page carrying JSON-LD, Microdata, and OpenGraph at once, the single
pass runs roughly nine to eleven times faster (pyperf, CPython 3.14 release build, Apple M4; reproduce with ``tox -e
bench structured``):

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - input
      - turbohtml
      - extruct
      - speed-up
    - - product page
      - 5.4 µs
      - 59.0 µs
      - 10.9x
    - - catalog (8 KiB)
      - 54.5 µs
      - 494.9 µs
      - 9.1x

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - extruct / metadata_parser
      - turbohtml
    - - ``extruct.extract(html)``
      - :meth:`~turbohtml.Document.structured_data`
    - - ``extruct.extract(html, syntaxes=["json-ld"])``
      - :meth:`~turbohtml.Document.json_ld`
    - - ``extruct.extract(html, syntaxes=["opengraph"])`` / ``MetadataParser(...).get_metadatas("og:...")``
      - :meth:`~turbohtml.Document.opengraph`
    - - ``extruct.extract(html, syntaxes=["microdata"])``
      - :meth:`~turbohtml.Document.microdata`
    - - ``extruct.extract(html, syntaxes=["rdfa", "microformat"])``
      - the :attr:`~turbohtml.StructuredData.rdfa` / :attr:`~turbohtml.StructuredData.microformats` fields of the
        :class:`~turbohtml.StructuredData` record (a later phase)

The helpers return plain Python objects that hold no reference back into the tree, so you can keep them after the
document is gone:

.. testcode::

    doc = parse('<script type="application/ld+json">{"@type": "Article", "name": "Hi"}</script>')
    print(doc.json_ld())

.. testoutput::

    [{'@type': 'Article', 'name': 'Hi'}]

**********
 Pitfalls
**********

- The OpenGraph result is a flat ``{key: value}`` mapping (the ``metadata_parser`` shape), not ``extruct``'s list of
  namespaced property tuples, and ``og:`` and ``twitter:`` tags share the one mapping because pages mix the ``property``
  and ``name`` attributes freely. When a key repeats, the last occurrence wins; read :meth:`~turbohtml.Document.json_ld`
  when you need every occurrence of a repeated key.
- :attr:`~turbohtml.StructuredData.rdfa` and :attr:`~turbohtml.StructuredData.microformats` are a later phase:
  :meth:`~turbohtml.Document.structured_data` returns those two fields as empty lists today, so code that reads them
  will not break when they land.
- A JSON-LD block whose body is not valid JSON is skipped rather than raising, matching ``extruct``'s default error
  handling; pass the raw ``<script>`` text to :mod:`json` yourself if you need to see the decode error.
