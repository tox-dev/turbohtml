######################
 From metadata_parser
######################

.. image:: https://static.pepy.tech/badge/metadata_parser/month
    :alt: metadata_parser monthly downloads
    :target: https://pepy.tech/project/metadata_parser

`metadata_parser <https://github.com/jvanasco/metadata_parser>`_ reads the social-card metadata a page advertises: the
OpenGraph (``og:``) and Twitter (``twitter:``) ``<meta>`` tags, plus the Dublin Core and plain ``name``/``content``
pairs. It builds a ``MetadataParser`` over the HTML and groups the tags into per-namespace dicts on
``parsed_result.metadata`` (``["og"]``, ``["twitter"]``, ``["dc"]``, ``["meta"]``) that you read by key.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Document.opengraph` returns the same social-card data as a flat ``{key: value}`` mapping, gathered in
one walk over the parsed document with no parser object to construct and no per-namespace accessor to pick. ``og:`` and
``twitter:`` tags share the one mapping because pages mix the ``property`` and ``name`` attributes freely, and the
result is a plain dict that holds no reference back into the tree:

.. testcode::

    from turbohtml import parse

    doc = parse('<head><meta property="og:title" content="Widget"><meta name="twitter:card" content="summary"></head>')
    print(doc.opengraph())

.. testoutput::

    {'og:title': 'Widget', 'twitter:card': 'summary'}

Both libraries start from the raw HTML string, so each parses before it reads the tags: ``metadata_parser`` builds its
own tree and maps the meta block in Python, where :meth:`~turbohtml.Document.opengraph` parses to the WHATWG tree and
gathers the ``og:``/``twitter:`` tags in one C walk. On a social-card head, and on an 8 KiB article carrying that head,
the single pass runs dozens of times faster:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - input
      - turbohtml
      - metadata_parser
    - - social-card head
      - 2.0 µs
      - 142 µs (71.6x)
    - - article (8 KiB)
      - 27.9 µs
      - 2.28 ms (81.8x)

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - metadata_parser
      - turbohtml
    - - ``mp = MetadataParser(html=html)``
      - ``doc = parse(html)`` (:func:`turbohtml.parse`)
    - - ``mp.parsed_result.metadata["og"]["title"]``
      - the ``og:title`` key of :meth:`~turbohtml.Document.opengraph`
    - - ``mp.parsed_result.get_metadatas("card", strategy=["twitter"])``
      - the ``twitter:card`` key of :meth:`~turbohtml.Document.opengraph`
    - - ``mp.get_metadata_link("image", strategy=["og"])``
      - the ``og:image`` key, absolutized yourself against :meth:`~turbohtml.Document.base_url`

**********
 Pitfalls
**********

- metadata_parser groups tags into per-namespace dicts with unprefixed keys (``metadata["og"]["title"]``), while
  :meth:`~turbohtml.Document.opengraph` returns one flat mapping with prefixed keys (``og:title``, ``twitter:card``)
  because pages mix the ``property`` and ``name`` attributes freely.
- :meth:`~turbohtml.Document.opengraph` returns the raw tag value; ``metadata_parser``'s ``get_metadata_link``
  absolutizes URL-valued tags against the page URL. Resolve those yourself with :meth:`~turbohtml.Document.base_url`.
- When a key repeats, the last occurrence wins in the flat mapping; read :meth:`~turbohtml.Document.structured_data`
  when you need every occurrence of a repeated key.
