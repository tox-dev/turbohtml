################
 From opengraph
################

.. package-meta:: opengraph-py3 erikriver/opengraph

`opengraph <https://pypi.org/project/opengraph-py3/>`_ (the ``opengraph_py3`` Python 3 fork of erikriver's
``opengraph``) reads a page's Open Graph card. ``OpenGraph(html=...)`` builds a BeautifulSoup tree over html5lib, scans
the head for ``og:``-prefixed ``<meta>`` tags, and returns a ``dict`` subclass keyed by the property name minus its
``og:`` prefix, with ``is_valid()`` reporting whether the required tags are present. ``OpenGraph(url=...)`` fetches the
page first; a ``scrape=True`` mode falls back to the ``<title>`` and body ``<img>`` when the og tags are missing.

It is a small, single-purpose scraping helper for lifting share-card metadata off a page. turbohtml serves that same
shape from :func:`turbohtml.extract.opengraph`, a string entry point over :meth:`turbohtml.Document.opengraph`: the
``og:`` ``<meta>`` tags are gathered in one C walk of the already parsed WHATWG tree, and the result is a frozen, fully
typed :class:`~turbohtml.extract.OpenGraph` mapping with the same prefix-stripped keys and the same ``is_valid`` check.

************************
 turbohtml vs opengraph
************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - opengraph
    - - Scope
      - Full WHATWG parser; Open Graph extraction is one feature of many
      - Single-purpose Open Graph card reader
    - - Feature breadth
      - ``og:`` mapping via :func:`~turbohtml.extract.opengraph`, plus ``twitter:`` tags and full structured data
        (JSON-LD, Microdata) off the same tree
      - ``og:`` tags only, with an optional ``scrape`` fallback to ``<title>`` and body ``<img>``
    - - Performance
      - One C walk of the parsed tree; 96-168x faster on a social card
      - BeautifulSoup tree over html5lib, scanned in Python
    - - Typing
      - Frozen, fully typed read-only :class:`~turbohtml.extract.OpenGraph` mapping, ``py.typed``
      - Plain ``dict`` subclass, no shipped type hints
    - - Dependencies
      - Zero runtime deps (self-contained C extension)
      - BeautifulSoup and html5lib, plus an HTTP client for ``url=`` mode
    - - Maintenance
      - Actively developed single extension
      - Small community fork, low cadence

Feature overlap
===============

The shared surface you can port one-to-one:

- ``OpenGraph(html=html)`` -> :func:`turbohtml.extract.opengraph`, one call returning the ``og:`` card.
- ``og["title"]`` / ``og.get("title")`` -> the same prefix-stripped keys on :class:`~turbohtml.extract.OpenGraph`;
  ``og:title`` reads as ``og["title"]``.
- ``og.is_valid()`` -> :meth:`turbohtml.extract.OpenGraph.is_valid`, the four-property presence check.
- ``"title" in og``, iteration, and equality against a plain ``dict`` all work, since
  :class:`~turbohtml.extract.OpenGraph` is a full :class:`~collections.abc.Mapping`.

What turbohtml adds
===================

- The ``twitter:`` card, which ``opengraph`` never reads: :meth:`turbohtml.Document.opengraph` returns the ``og:`` and
  ``twitter:`` tags together (with prefixes kept) off the same walk.
- Every other structured-data format on the same tree: :meth:`~turbohtml.Document.json_ld`,
  :meth:`~turbohtml.Document.microdata`, and :meth:`~turbohtml.Document.structured_data` for the whole set at once.
- A read-only mapping that holds no reference back into the tree, so it outlives the document it came from.
- One C walk under the per-tree critical section, 96-168x faster than the BeautifulSoup scan.
- Zero third-party runtime dependencies: no BeautifulSoup, no html5lib to install or pin.
- Full type coverage: :class:`~turbohtml.extract.OpenGraph` is annotated and shipped with ``py.typed``.

What opengraph has that turbohtml does not
==========================================

- **URL fetching** (``OpenGraph(url=...)``): no equivalent. turbohtml does not fetch; run your own HTTP client and pass
  the response body to :func:`~turbohtml.extract.opengraph`.
- **Scrape fallback** (``scrape=True``, which falls back to ``<title>`` and body ``<img>`` when the og tags are
  missing): no equivalent. Select those elements yourself with :meth:`turbohtml.Node.find` when the card is incomplete.
- **``to_json()`` / ``to_html()`` serializers**: no equivalent. Build them from ``dict(og)`` yourself with :mod:`json`
  or your own markup.

Performance
===========

Both libraries start from the raw HTML string, so each parses before it reads the tags: ``opengraph`` builds a
BeautifulSoup tree over html5lib and scans the head in Python, where :func:`~turbohtml.extract.opengraph` parses to the
WHATWG tree and gathers the ``og:`` tags in one C walk. On a social-card head, and on an 8 KiB article carrying that
head, the walk runs 96 to 168 times faster:

.. bench-table::
    :file: bench/opengraph.json

Across hand-built cards covering the protocol's basic, structured-image, and required-tag shapes, the two libraries
return byte-identical mappings.

****************
 How to migrate
****************

Swap the import and the constructor for a single function call on the HTML you already have.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `opengraph <https://pypi.org/project/opengraph-py3/>`__
      - turbohtml
    - - ``OpenGraph(html=html)``
      - :func:`turbohtml.extract.opengraph`
    - - ``OpenGraph(url=url)``
      - :func:`~turbohtml.extract.opengraph` on the HTML you fetch yourself
    - - ``og["title"]`` / ``og.get("title")``
      - the same keys on :class:`~turbohtml.extract.OpenGraph`
    - - ``og.is_valid()``
      - :meth:`turbohtml.extract.OpenGraph.is_valid`
    - - ``og.to_json()`` / ``og.to_html()``
      - build these from ``dict(og)`` yourself

Before, with ``opengraph``, the constructor parses and the dict subclass carries the prefix-stripped keys:

.. code-block:: python

    from opengraph import OpenGraph

    og = OpenGraph(
        html="<head>"
        '<meta property="og:title" content="The Rock">'
        '<meta property="og:type" content="video.movie">'
        '<meta property="og:image" content="https://x/rock.jpg">'
        '<meta property="og:url" content="https://x/tt0117500/">'
        '<meta name="twitter:card" content="summary">'
        "</head>"
    )
    print(og["title"])
    print(dict(og))
    print(og.is_valid())

After, :func:`~turbohtml.extract.opengraph` returns the same shape from one C walk, as a read-only mapping that keeps no
reference into the tree:

.. testcode::

    from turbohtml.extract import opengraph

    og = opengraph(
        "<head>"
        '<meta property="og:title" content="The Rock">'
        '<meta property="og:type" content="video.movie">'
        '<meta property="og:image" content="https://x/rock.jpg">'
        '<meta property="og:url" content="https://x/tt0117500/">'
        '<meta name="twitter:card" content="summary">'
        "</head>"
    )
    print(og["title"])
    print(dict(og))
    print(og.is_valid())

.. testoutput::

    The Rock
    {'title': 'The Rock', 'type': 'video.movie', 'image': 'https://x/rock.jpg', 'url': 'https://x/tt0117500/'}
    True

turbohtml does not fetch URLs; ``OpenGraph(url=...)`` becomes your HTTP client plus :func:`~turbohtml.extract.opengraph`
on the response body. A card missing a required property reads back fine but fails the validity check:

.. testcode::

    og = opengraph('<head><meta property="og:title" content="Only a title"></head>')
    print(og.get("title"), og.is_valid())

.. testoutput::

    Only a title False

**********************
 Gotchas and pitfalls
**********************

- Keys drop the ``og:`` prefix (``og["title"]``, not ``og["og:title"]``), matching ``opengraph`` and diverging from the
  prefixed keys :meth:`turbohtml.Document.opengraph` returns. Structured sub-properties keep their tail, so
  ``og:image:width`` reads as ``og["image:width"]``.
- :func:`~turbohtml.extract.opengraph` drops the ``twitter:`` tags that :meth:`turbohtml.Document.opengraph` also
  returns, because ``opengraph`` only reads ``og:``-prefixed properties. Call :meth:`~turbohtml.Document.opengraph` when
  you want the Twitter card too.
- :meth:`~turbohtml.extract.OpenGraph.is_valid` requires the `Open Graph protocol <https://ogp.me/>`_'s four basic
  properties (``og:title``, ``og:type``, ``og:image``, ``og:url``). The ``opengraph_py3`` fork additionally requires
  ``og:description``, so a card with the four but no description is valid here and invalid there.
- The mapping is read-only. ``opengraph`` returns a mutable ``dict`` subclass you can assign into;
  :class:`~turbohtml.extract.OpenGraph` supports only the read surface, so copy into ``dict(og)`` first if you need to
  edit.
- turbohtml takes an already-decoded ``str`` and applies the WHATWG encoding rules; ``opengraph`` in ``url=`` mode
  decodes the fetched bytes itself. Decode the response body to ``str`` before handing it over rather than passing raw
  bytes.
