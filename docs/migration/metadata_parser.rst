######################
 From metadata_parser
######################

.. package-meta:: metadata_parser jvanasco/metadata_parser

`metadata_parser <https://github.com/jvanasco/metadata_parser>`_ reads the social-card metadata a page advertises: the
OpenGraph (``og:``) and Twitter (``twitter:``) ``<meta>`` tags, plus Dublin Core (``dc``), plain ``name``/``content``
pairs, and page-level links such as ``canonical`` and ``shortlink``. It builds a ``MetadataParser`` over the HTML (or
fetches it for you from a URL), parses with BeautifulSoup, and groups the tags into per-namespace dicts on
``parsed_result.metadata`` (``["og"]``, ``["twitter"]``, ``["dc"]``, ``["meta"]``, ``["page"]``) that you read by key or
through ``get_metadata``/``get_metadata_link`` with a namespace ``strategy``. Its extra weight is URL work: it fetches
pages, follows redirects, and resolves relative URL-valued tags against the page URL to pick a single "discrete" URL for
sharing.

turbohtml covers the social-card ground with :meth:`~turbohtml.Document.opengraph`, which gathers the ``og:`` and
``twitter:`` ``<meta>`` tags into one flat ``{key: value}`` mapping in a single C walk over the WHATWG-parsed tree, with
no parser object to construct and no per-namespace accessor to pick. It parses the markup you already have rather than
fetching, so it slots behind whatever downloader you use.

******************************
 turbohtml vs metadata_parser
******************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - metadata_parser
    - - Scope
      - WHATWG HTML parser with metadata extraction on parsed trees
      - Social-card metadata reader with URL fetching and normalization
    - - Feature breadth
      - Full DOM, selectors, serialization, ``opengraph()``, ``dublin_core()``, ``rdfa()``, ``structured_data()``,
        ``base_url()``
      - ``og``/``twitter``/``dc``/``meta``/``page`` namespaces, URL fetch, redirect and canonical-URL resolution
    - - Performance
      - One C walk over the parsed tree (see below)
      - BeautifulSoup tree build and Python namespace mapping
    - - Typing
      - Fully typed, ships stubs
      - No published type stubs
    - - Dependencies
      - Zero runtime dependencies (native C extension)
      - requests, BeautifulSoup4, and helper libraries
    - - Maintenance
      - Actively maintained
      - Actively maintained

Feature overlap
===============

Port these 1:1:

- ``mp.parsed_result.metadata["og"]["title"]`` -> the ``og:title`` key of :meth:`~turbohtml.Document.opengraph`.
- ``mp.parsed_result.get_metadatas("card", strategy=["twitter"])`` -> the ``twitter:card`` key of
  :meth:`~turbohtml.Document.opengraph`.
- ``mp.parsed_result.metadata["twitter"]["image"]`` -> the ``twitter:image`` key of the same mapping (``og:`` and
  ``twitter:`` tags share the one dict).
- ``MetadataParser(html=html)`` -> ``parse(html)`` (:func:`turbohtml.parse`), then read
  :meth:`~turbohtml.Document.opengraph`.

What turbohtml adds
===================

- A full WHATWG-conformant parser: the tree :meth:`~turbohtml.Document.opengraph` walks is the same DOM you query with
  CSS selectors, serialize, or mutate, not a metadata-private intermediate.
- Zero runtime dependencies. metadata_parser pulls in requests and BeautifulSoup4; turbohtml is a self-contained C
  extension.
- Full type coverage with shipped stubs, so ``opengraph()`` and its result check under a type checker.
- Structured data beyond the social card: :meth:`~turbohtml.Document.structured_data` returns JSON-LD, Microdata, RDFa,
  and Dublin Core in the same walk, with :meth:`~turbohtml.Document.json_ld`, :meth:`~turbohtml.Document.microdata`,
  :meth:`~turbohtml.Document.rdfa`, and :meth:`~turbohtml.Document.dublin_core` beside
  :meth:`~turbohtml.Document.opengraph`.
- Document URL hints on the parsed tree: :meth:`~turbohtml.Document.base_url` resolves the effective ``<base>`` and
  :meth:`~turbohtml.Document.meta_refresh` reads the refresh target.
- A standalone string entry point :func:`turbohtml.extract.opengraph` that strips the ``og:`` prefix and adds
  ``is_valid()`` for the Open Graph protocol's required properties.

What metadata_parser has that turbohtml does not
================================================

- **URL fetching**: ``MetadataParser(url=...)`` downloads the page, follows redirects, and detects the response
  encoding. turbohtml takes parsed HTML only, so pair it with ``urllib`` or ``httpx``.
- **Generic ``name``/``content`` namespace**: metadata_parser groups arbitrary ``<meta name>`` tags into
  ``metadata["meta"]``. :meth:`~turbohtml.Document.opengraph` gathers only ``og:``/``twitter:`` tags and
  :meth:`~turbohtml.Document.dublin_core` the ``dc.*``/``dcterms.*`` ones; read any other ``<meta>`` by selecting it,
  e.g. ``parse(html).find_all("meta")``.
- **Page-level links** (``canonical``, ``shortlink``) collected into ``metadata["page"]``. Read those with a selector,
  e.g. ``parse(html).find('link[rel="canonical"]')``.
- **Namespace ``strategy`` ordering**: ``get_metadata(field, strategy=[...])`` picks the first namespace that carries a
  field. turbohtml keys tags by their full prefix (``og:title`` vs ``twitter:title``), so read the prefix you want
  directly.

Performance
===========

Both libraries start from the raw HTML string, so each parses before it reads the tags: ``metadata_parser`` builds its
own tree and maps the meta block in Python, where :meth:`~turbohtml.Document.opengraph` parses to the WHATWG tree and
gathers the ``og:``/``twitter:`` tags in one C walk. On a social-card head, and on an 8 KiB article carrying that head,
the single pass runs dozens of times faster:

.. bench-table::
    :file: bench/metadata_parser.json

****************
 How to migrate
****************

Swap the ``MetadataParser`` construction for a :func:`turbohtml.parse` call, then read
:meth:`~turbohtml.Document.opengraph` by prefixed key instead of picking a namespace dict. Where metadata_parser fetched
the URL for you, download the markup first and pass it to :func:`turbohtml.parse`.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `metadata_parser <https://github.com/jvanasco/metadata_parser>`__
      - turbohtml
    - - ``mp = MetadataParser(html=html)``
      - ``doc = parse(html)`` (:func:`turbohtml.parse`)
    - - ``mp.parsed_result.metadata["og"]["title"]``
      - the ``og:title`` key of :meth:`~turbohtml.Document.opengraph`
    - - ``mp.parsed_result.get_metadatas("card", strategy=["twitter"])``
      - the ``twitter:card`` key of :meth:`~turbohtml.Document.opengraph`
    - - ``mp.get_metadata_link("image", strategy=["og"])``
      - the ``og:image`` key of ``doc.opengraph(base_url=page_url)``, absolutized against the page URL
    - - ``mp.parsed_result.metadata["dc"]``
      - :meth:`~turbohtml.Document.dublin_core` (the ``dc.*``/``dcterms.*`` names, lower-cased)
    - - ``mp.parsed_result.metadata["meta"]``
      - ``doc.find_all("meta")``, read ``name``/``content`` off each tag

.. testcode::

    from turbohtml import parse

    doc = parse('<head><meta property="og:title" content="Widget"><meta name="twitter:card" content="summary"></head>')
    print(doc.opengraph())

.. testoutput::

    {'og:title': 'Widget', 'twitter:card': 'summary'}

**********************
 Gotchas and pitfalls
**********************

- metadata_parser groups tags into per-namespace dicts with unprefixed keys (``metadata["og"]["title"]``), while
  :meth:`~turbohtml.Document.opengraph` returns one flat mapping with prefixed keys (``og:title``, ``twitter:card``)
  because pages mix the ``property`` and ``name`` attributes freely.
- :meth:`~turbohtml.Document.opengraph` returns the raw tag value by default; pass ``base_url=`` (the page URL) to
  absolutize the URL-valued tags (``og:url``, ``og:image``, ``og:video``, ``twitter:image``, ...) against it, the way
  ``metadata_parser``'s ``get_metadata_link`` and ``get_discrete_url`` do. A ``<base href>`` refines the base URL.
- When a key repeats, the last occurrence wins in the flat mapping; read :meth:`~turbohtml.Document.structured_data`
  when you need every occurrence of a repeated key.
- metadata_parser can fetch (``MetadataParser(url=...)``) and detect the response encoding; turbohtml takes parsed HTML,
  so fetch the page yourself and pass the markup to :func:`turbohtml.parse`.
- Only ``og:`` and ``twitter:`` tags land in :meth:`~turbohtml.Document.opengraph`; Dublin Core comes back through
  :meth:`~turbohtml.Document.dublin_core`. Generic ``name``/``content`` pairs and page-level ``<link>`` tags come back
  through selectors, not the metadata mapping.
