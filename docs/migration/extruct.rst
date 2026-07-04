##############
 From extruct
##############

.. package-meta:: extruct scrapinghub/extruct

`extruct <https://github.com/scrapinghub/extruct>`_ pulls the machine-readable metadata a page embeds: JSON-LD,
Microdata, RDFa, microformats, the OpenGraph/Twitter card tags, and Dublin Core. You call one ``extract(html)`` entry
point, optionally naming a ``syntaxes`` list, and get back a dict keyed by syntax name. Under the hood it builds an lxml
tree once and then runs a separate extractor per syntax you asked for, each on top of ``lxml``, ``w3lib``, ``mf2py``,
and an RDFa/DublinCore stack.

It is a scraping-pipeline staple, developed by Zyte (formerly Scrapinghub) and used alongside Scrapy to lift structured
product, article, and event data off crawled pages. turbohtml covers the same ground from the other direction: it is a
WHATWG HTML parser first, and :meth:`~turbohtml.Document.structured_data` reads every supported format off the already
parsed tree in a single C walk, so extraction is not a second tree build but a walk of the one you already have.

**********************
 turbohtml vs extruct
**********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - extruct
    - - Scope
      - Full WHATWG parser; structured data is one feature of many
      - Focused metadata extractor, one job
    - - Feature breadth
      - JSON-LD, Microdata, OpenGraph/Twitter, RDFa, Dublin Core; microformats reserved (empty list)
      - JSON-LD, Microdata, RDFa, OpenGraph, microformats, Dublin Core
    - - Performance
      - One C walk of the parsed tree; ~9-11x faster on a combined page
      - Builds an lxml tree, then one extractor per requested syntax
    - - Typing
      - Frozen, fully typed records (:class:`~turbohtml.StructuredData`, :class:`~turbohtml.MicrodataItem`),
        ``py.typed``
      - Plain dicts and lists, no shipped type hints
    - - Dependencies
      - Zero runtime deps (self-contained C extension)
      - ``lxml``, ``w3lib``, ``mf2py``, plus an RDFa/DublinCore stack
    - - Maintenance
      - Actively developed single extension
      - Mature, Zyte-backed, slower cadence

Feature overlap
===============

The shared surface you can port one-to-one:

- ``extruct.extract(html)`` -> :meth:`~turbohtml.Document.structured_data`, one call gathering every format.
- ``syntaxes=["json-ld"]`` -> :meth:`~turbohtml.Document.json_ld`, each ``<script type="application/ld+json">`` block
  decoded with the standard library :mod:`json`.
- ``syntaxes=["opengraph"]`` -> :meth:`~turbohtml.Document.opengraph`, the ``og:``/``twitter:`` ``<meta>`` tags.
- ``syntaxes=["microdata"]`` -> :meth:`~turbohtml.Document.microdata`, returning :class:`~turbohtml.MicrodataItem`
  records with ``itemtype``, ``itemid``, and the nested ``itemprop`` values.
- Both skip a malformed JSON-LD block rather than raising, the forgiving default ``extruct`` also takes.

What turbohtml adds
===================

- A single walk over the parsed tree returns every format at once as a frozen, fully typed
  :class:`~turbohtml.StructuredData` record whose six fields you read by attribute, so reading a result never depends on
  which extractors you enabled.
- The locating runs in the C core under the per-tree critical section; only JSON-LD decoding stays in Python.
- The records hold no reference back into the tree, so they outlive the document they came from.
- Standalone string entry points :func:`turbohtml.extract.microdata` and :func:`turbohtml.extract.opengraph` cover the
  ``microdata`` and ``opengraph`` libraries' call shapes on the same C walk, so an extruct migration also folds those
  in.
- No third-party runtime dependency: no lxml, w3lib, or mf2py to install or pin.

What turbohtml also covers now
==============================

- **RDFa**: :meth:`~turbohtml.Document.structured_data` fills :attr:`~turbohtml.StructuredData.rdfa` with
  :class:`~turbohtml.RdfaItem` records (also via :meth:`~turbohtml.Document.rdfa`). Rather than extruct's expanded
  JSON-LD triples, turbohtml returns the Microdata item shape -- ``type``/``resource``/``properties`` -- with
  ``property`` keys and ``typeof`` IRIs expanded against the in-scope ``@vocab`` and ``@prefix`` (the RDFa 1.1 initial
  context seeds the well-known prefixes).
- **Dublin Core**: :meth:`~turbohtml.Document.dublin_core` (and :attr:`~turbohtml.StructuredData.dublin_core`) maps the
  ``dc.*``/``dcterms.*`` ``<meta>`` names, lower-cased, to their content.

What extruct has that turbohtml does not
========================================

- **microformats**: a documented later phase. :meth:`~turbohtml.Document.structured_data` returns the
  :attr:`~turbohtml.StructuredData.microformats` field as an empty list today. Keep extruct for that syntax until the
  phase lands; the field name is already in place so code that reads it will not break.
- **RDFa as expanded triples**: extruct returns RDFa as JSON-LD-expanded triples via ``pyRdfa``; turbohtml returns the
  Microdata-shaped :class:`~turbohtml.RdfaItem` instead, and does not expand CURIEs embedded in arbitrary IRIs or
  surface the ``datatype`` type IRI (typed literals come back as their lexical string).
- **``uniform`` / ``return_html_node`` options**: no equivalent. turbohtml's output shape is fixed per format; there is
  no normalized cross-syntax view or embedded lxml node handle.

Performance
===========

``extruct`` starts from the raw HTML string, so it parses before it extracts: it builds an lxml tree and runs a separate
extractor per syntax, where :meth:`~turbohtml.Document.structured_data` parses to the WHATWG tree and gathers every
format in one C walk. On a product page carrying JSON-LD, Microdata, and OpenGraph at once, the single pass runs roughly
nine to eleven times faster:

.. bench-table::
    :file: bench/extruct.json

****************
 How to migrate
****************

Swap the import and the extractor call for a parse plus a method on the document.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `extruct <https://github.com/scrapinghub/extruct>`__
      - turbohtml
    - - ``extruct.extract(html)``
      - :meth:`~turbohtml.Document.structured_data`
    - - ``extruct.extract(html, syntaxes=["json-ld"])``
      - :meth:`~turbohtml.Document.json_ld`
    - - ``extruct.extract(html, syntaxes=["opengraph"])``
      - :meth:`~turbohtml.Document.opengraph`
    - - ``extruct.extract(html, syntaxes=["microdata"])``
      - :meth:`~turbohtml.Document.microdata`
    - - ``extruct.extract(html, syntaxes=["rdfa"])``
      - :meth:`~turbohtml.Document.rdfa` (Microdata-shaped :class:`~turbohtml.RdfaItem`, not expanded triples)
    - - ``extruct.extract(html, syntaxes=["dublincore"])``
      - :meth:`~turbohtml.Document.dublin_core`
    - - ``extruct.extract(html, syntaxes=["microformat"])``
      - the :attr:`~turbohtml.StructuredData.microformats` field of the :class:`~turbohtml.StructuredData` record (a
        later phase)

Before, with extruct, each syntax comes back under a string key in a dict:

.. code-block:: python

    import extruct

    html = (
        '<head><meta property="og:title" content="Widget"></head>'
        '<body><script type="application/ld+json">{"@type": "Product"}</script>'
        '<div itemscope itemtype="https://schema.org/Offer"><span itemprop="price">9.99</span></div></body>'
    )
    data = extruct.extract(html, syntaxes=["json-ld", "opengraph", "microdata"])
    print(data["json-ld"])
    print(data["opengraph"])
    print(data["microdata"])

After, one walk returns a typed record whose fields you read by attribute:

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

The per-format helpers return the same plain objects, holding no reference back into the tree, so you can keep them
after the document is gone:

.. testcode::

    doc = parse('<script type="application/ld+json">{"@type": "Article", "name": "Hi"}</script>')
    print(doc.json_ld())

.. testoutput::

    [{'@type': 'Article', 'name': 'Hi'}]

**********************
 Gotchas and pitfalls
**********************

- The OpenGraph result is a flat ``{key: value}`` mapping, not ``extruct``'s list of namespaced property tuples, and
  ``og:`` and ``twitter:`` tags share the one mapping because pages mix the ``property`` and ``name`` attributes freely.
  When a key repeats, the last occurrence wins; read :meth:`~turbohtml.Document.json_ld` when you need every occurrence
  of a repeated key.
- :attr:`~turbohtml.StructuredData.microformats` is a later phase: :meth:`~turbohtml.Document.structured_data` returns
  it as an empty list today, so code that reads it will not break when it lands, but the values are not there yet. RDFa
  and Dublin Core are populated, in :attr:`~turbohtml.StructuredData.rdfa` and
  :attr:`~turbohtml.StructuredData.dublin_core`.
- RDFa ``property`` keys and ``typeof`` IRIs are expanded against the in-scope ``@vocab``/``@prefix`` (with the RDFa 1.1
  initial context seeding the common prefixes); an undeclared prefix or a bare term with no vocabulary in scope stays
  verbatim. Pass ``base_url=`` to :meth:`~turbohtml.Document.rdfa` to absolutize the ``resource``/``href``/``src`` IRIs.
- A JSON-LD block whose body is not valid JSON is skipped rather than raising, matching ``extruct``'s default error
  handling; ``extruct``'s ``errors="strict"`` mode has no turbohtml equivalent. Pass the raw ``<script>`` text to
  :mod:`json` yourself if you need to see the decode error. A block whose payload is a scalar or ``null`` is also
  dropped, since it is not a JSON-LD node object.
- Extracted URL values are returned verbatim by default. Pass ``base_url=`` to
  :meth:`~turbohtml.Document.structured_data` (or to :meth:`~turbohtml.Document.opengraph` /
  :meth:`~turbohtml.Document.microdata`) to absolutize the Microdata and OpenGraph URL-valued fields against it, the way
  ``extruct``'s ``base_url`` argument resolves them; a ``<base href>`` refines it. JSON-LD is left verbatim, so resolve
  any ``@id`` yourself with :func:`urllib.parse.urljoin`.
- turbohtml decodes the string to the WHATWG tree; ``extruct`` decodes bytes with ``w3lib`` per its ``encoding``
  argument. Hand turbohtml already-decoded ``str`` and let it apply the WHATWG encoding rules rather than pre-forcing a
  codec.
