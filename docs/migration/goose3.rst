#############
 From goose3
#############

.. package-meta:: goose3 goose3/goose3

`goose3 <https://goose3.readthedocs.io>`_ is an article extractor in the newspaper mold: ``Goose().extract(url=...)``
(or ``raw_html=...``) fetches a page, scores the content body on an lxml tree, runs its cleaners over the winner, and
returns an ``Article`` carrying the cleaned text alongside the harvested metadata, images, and embedded media. It grew
out of the original Java Goose and targets scraping pipelines that need the main story out of a news or blog page
without hand-writing per-site selectors.

turbohtml covers that ground with :meth:`~turbohtml.Node.article`: content-density scoring picks the body and a metadata
harvest fills the :class:`~turbohtml.Article` record in one C pass over the parsed tree. It parses the markup you
already have rather than fetching, so it slots into the same scraping pipeline behind whatever downloader you already
use.

*********************
 turbohtml vs goose3
*********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - goose3
    - - Scope
      - WHATWG HTML parser with article extraction on parsed trees
      - Article extractor: fetch, score, clean, and harvest metadata
    - - Feature breadth
      - Full DOM, selectors, serialization, ``article()``, ``opengraph()``, ``links()``
      - Content scoring plus images, movies/tweets, and language-specific stopwords
    - - Performance
      - One C pass over the parsed tree
      - lxml tree build, scoring, and a Python cleaner chain (see below)
    - - Typing
      - Fully typed, ships stubs
      - No published type stubs
    - - Dependencies
      - Zero runtime dependencies (native C extension)
      - lxml, requests, Pillow, and language/parsing helpers
    - - Maintenance
      - Actively maintained
      - Community-maintained continuation of python-goose

Feature overlap
===============

Port these 1:1:

- ``Goose().extract(raw_html=html)`` -> ``parse(html).article()``.
- ``article.cleaned_text`` -> ``article().text`` (layout-aware plain text of the scored body).
- ``article.top_node`` -> ``article().element`` (the scored element, ``None`` when nothing reads as content).
- ``article.title`` -> ``article().title``.
- ``article.authors`` -> ``article().byline`` (see the pitfalls on the list-vs-string difference).
- ``article.publish_date`` -> ``article().date``.
- ``article.meta_description`` -> ``article().description``.
- ``article.meta_lang`` -> ``article().lang``.
- ``article.opengraph`` -> :meth:`doc.opengraph() <turbohtml.Document.opengraph>`.
- ``article.links`` -> :meth:`doc.links() <turbohtml.Node.links>`.

What turbohtml adds
===================

- A full WHATWG-conformant parser: the tree ``article()`` scores is the same DOM you query with CSS selectors,
  serialize, or mutate, not a scraper-private intermediate.
- Zero runtime dependencies. goose3 pulls in lxml, requests, and Pillow; turbohtml is a self-contained C extension.
- Full type coverage with shipped stubs, so ``article()`` and its :class:`~turbohtml.Article` fields check under a type
  checker.
- The same extraction primitives outside article context: :meth:`doc.opengraph() <turbohtml.Document.opengraph>` and
  :meth:`doc.links() <turbohtml.Node.links>` work on any parsed document.

What goose3 has that turbohtml does not
=======================================

- Fetching: ``extract(url=...)`` downloads the page. turbohtml takes parsed HTML only, so pair it with ``urllib`` or
  ``httpx``.
- Image extraction (``top_image``, ``infographic``) and the Pillow-backed candidate scoring. No turbohtml equivalent.
- Embedded media harvesting (``movies``, ``tweets``). No turbohtml equivalent.
- Language-specific stopword configuration used to tune scoring per language. turbohtml scores on content density
  without a per-language stopword list.
- ``authors`` as a full list of names. ``article().byline`` keeps only the first author source as one string.

Performance
===========

Extracting the content body and metadata from a full page -- navigation, a scored article, and a footer.
:meth:`~turbohtml.Node.article` scores and harvests in one C pass over the parsed tree; goose3 builds an lxml tree,
scores it, and runs its cleaner chain in Python. Numbers vary with input and hardware.

.. bench-table::
    :file: bench/goose3.json

****************
 How to migrate
****************

Swap the fetch-and-extract call for a parse-then-``article()`` call. Where goose3 fetched the URL for you, download the
markup first and pass it to :func:`turbohtml.parse`.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `goose3 <https://goose3.readthedocs.io/>`__
      - turbohtml
    - - ``Goose().extract(raw_html=html)``
      - ``parse(html).article()``
    - - ``article.title``
      - ``article().title``
    - - ``article.cleaned_text``
      - ``article().text``
    - - ``article.authors`` (a list)
      - ``article().byline`` (the first author source, one string)
    - - ``article.publish_date``
      - ``article().date``
    - - ``article.meta_description``
      - ``article().description``
    - - ``article.meta_lang``
      - ``article().lang``
    - - ``article.top_node``
      - ``article().element`` (the scored element, ``None`` when nothing reads as content)
    - - ``article.opengraph``
      - :meth:`doc.opengraph() <turbohtml.Document.opengraph>`
    - - ``article.links``
      - :meth:`doc.links() <turbohtml.Node.links>`

.. testcode::

    from turbohtml import parse

    doc = parse(
        "<html lang=en><head><title>Comets</title>"
        "<meta property=article:published_time content='2024-05-06'>"
        "<meta name=description content='A short guide to comets.'></head>"
        "<body><article class=post><h1>Comets</h1>"
        "<p>By <a rel=author href='/u'>Ada Lovelace</a></p>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "</article></body></html>"
    )
    art = doc.article()
    print(art.title, "|", art.byline, "|", art.date, "|", art.description)

.. testoutput::

    Comets | Ada Lovelace | 2024-05-06 | A short guide to comets.

**********************
 Gotchas and pitfalls
**********************

- goose3 downloads URLs (``extract(url=...)``); :meth:`~turbohtml.Node.article` takes parsed HTML. Fetch the page
  yourself and pass the markup to :func:`turbohtml.parse`.
- ``article().byline`` is one whitespace-folded string from the first author source; goose's ``authors`` is a list, so a
  multi-author page keeps only the first name.
- Both report the date the page declares, from different preferred sources (a ``<time>`` first for turbohtml,
  ``article:published_time`` first for goose), so the same page can answer with two spellings of the same instant.
- ``article().title`` prefers the first ``<h1>``, then ``og:title``, then ``<title>``; goose starts from ``<title>`` and
  splits site-name suffixes off, so pages whose ``<h1>`` and ``<title>`` disagree yield different titles.
- goose's image (``top_image``, ``infographic``) and embed (``movies``, ``tweets``) extraction, its language-specific
  stopword configuration, and video handling have no turbohtml equivalent and are out of scope.
