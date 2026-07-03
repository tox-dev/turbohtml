################
 From opengraph
################

.. package-meta:: opengraph-py3 erikriver/opengraph

`opengraph <https://pypi.org/project/opengraph-py3/>`_ (the ``opengraph_py3`` Python 3 fork of erikriver's
``opengraph``) reads a page's Open Graph card: ``OpenGraph(html=...)`` builds a BeautifulSoup tree, scans the head for
``og:``-prefixed ``<meta>`` tags, and returns a ``dict`` subclass keyed by the property name minus its ``og:`` prefix,
with ``is_valid()`` reporting whether the required tags are present. turbohtml serves that shape from
:func:`turbohtml.extract.opengraph` over :meth:`turbohtml.Document.opengraph`.

***************
 Why turbohtml
***************

:func:`turbohtml.extract.opengraph` returns an :class:`~turbohtml.extract.OpenGraph` mapping with the same
prefix-stripped keys, so ``og["title"]`` reads the ``og:title`` tag, and the same
:meth:`~turbohtml.extract.OpenGraph.is_valid` check. It is a read-only mapping, so ``in``, ``.get``, iteration, and
equality against a plain ``dict`` all work, and it holds no reference back into the tree:

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

Both libraries start from the raw HTML string, so each parses before it reads the tags: ``opengraph`` builds a
BeautifulSoup tree (over html5lib) and scans the head in Python, where :func:`~turbohtml.extract.opengraph` parses to
the WHATWG tree and gathers the ``og:`` tags in one C walk. On a social-card head, and on an 8 KiB article carrying that
head, the walk runs 94 to 133 times faster:

.. bench-table::
    :file: bench/opengraph.json

Across hand-built cards covering the protocol's basic, structured-image, and required-tag shapes, the two libraries
return byte-identical mappings.

*************
 The renames
*************

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

turbohtml does not fetch URLs; ``OpenGraph(url=...)`` becomes your HTTP client plus :func:`~turbohtml.extract.opengraph`
on the response body:

.. testcode::

    og = opengraph('<head><meta property="og:title" content="Only a title"></head>')
    print(og.get("title"), og.is_valid())

.. testoutput::

    Only a title False

**********
 Pitfalls
**********

- Keys drop the ``og:`` prefix (``og["title"]``, not ``og["og:title"]``), matching ``opengraph`` and diverging from the
  prefixed keys :meth:`turbohtml.Document.opengraph` returns. Structured sub-properties keep their tail, so
  ``og:image:width`` reads as ``og["image:width"]``.
- :func:`~turbohtml.extract.opengraph` drops the ``twitter:`` tags that :meth:`turbohtml.Document.opengraph` also
  returns, because ``opengraph`` only reads ``og:``-prefixed properties. Call :meth:`~turbohtml.Document.opengraph` when
  you want the Twitter card too.
- :meth:`~turbohtml.extract.OpenGraph.is_valid` requires the `Open Graph protocol <https://ogp.me/>`_'s four basic
  properties (``og:title``, ``og:type``, ``og:image``, ``og:url``). The ``opengraph_py3`` fork additionally requires
  ``og:description``, so a card with the four but no description is valid here and invalid there.
- ``OpenGraph`` scraping mode (``scrape=True``, which falls back to ``<title>`` and body ``<img>`` when og tags are
  missing) has no equivalent; select those elements yourself with :meth:`~turbohtml.Node.find` when the card is
  incomplete.
