##################
 From newspaper3k
##################

.. package-meta:: newspaper3k codelucas/newspaper

`newspaper3k <https://newspaper.readthedocs.io>`_ is a news-article scraper built on ``requests`` and ``lxml``. An
``Article`` downloads a URL, then ``parse()`` fills ``article.text``, ``article.title``, ``article.authors``,
``article.publish_date``, ``article.meta_description`` and the rest by scoring the ``lxml`` tree and running
regex-driven metadata scans. On top of extraction it bundles fetching, per-source article discovery, multi-language
stopword lists, and NLP (``nlp()`` for keywords and a summary). It is widely used for one-shot "give me the body of this
news page" scripts and small crawlers.

turbohtml covers the extraction half of that surface. :meth:`~turbohtml.Node.article` scores the parsed tree and
harvests the same metadata in one C pass over a WHATWG-conformant DOM, returning a typed :class:`~turbohtml.Article`
record. It does not fetch URLs and has no NLP; you bring the markup and turbohtml gives back the content and metadata.

**************************
 turbohtml vs newspaper3k
**************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - newspaper3k
    - - Scope
      - Parse plus content and metadata extraction from markup you supply
      - Fetch, extract, source discovery, and NLP end to end
    - - Feature breadth
      - Content body, ``title``, ``byline``, ``date``, ``description``, ``lang``; plus a full CSS/XPath DOM
      - The same fields (authors as a list) plus downloading, keywords, and summarization
    - - Performance
      - One C pass over the arena; see the table below
      - Builds an ``lxml`` tree, then regex metadata scans
    - - Typing
      - Typed API, :class:`~turbohtml.Article` is a ``NamedTuple`` with shipped stubs
      - Untyped; attributes are populated after ``parse()``
    - - Dependencies
      - Self-contained C extension, no Python runtime deps
      - ``requests``, ``lxml``, ``beautifulsoup4``, ``feedparser``, ``nltk``, ``Pillow`` and more
    - - Maintenance
      - Actively maintained
      - Original ``newspaper3k`` is unmaintained; a community ``newspaper4k`` fork continues it

Feature overlap
===============

The extraction fields map 1:1 onto the :class:`~turbohtml.Article` record from :meth:`~turbohtml.Node.article`:

- ``article.text`` -> ``doc.article().text`` (or the :meth:`~turbohtml.Node.main_text` shortcut)
- ``article.title`` -> ``doc.article().title``
- ``article.authors`` -> ``doc.article().byline`` (one string, not a list)
- ``article.publish_date`` -> ``doc.article().date``
- ``article.meta_description`` -> ``doc.article().description``
- ``article.meta_lang`` -> ``doc.article().lang``
- ``article.top_node`` -> ``doc.article().element`` (the scored element, or ``None``)

What turbohtml adds
===================

- A WHATWG-conformant parser, so malformed markup produces the same tree a browser would rather than lxml's recovery.
- CSS selector and XPath querying over the parsed tree, so you can pull fields newspaper does not expose without a
  second library.
- A typed surface: :class:`~turbohtml.Article` is a ``NamedTuple`` with shipped stubs, so field access is checked.
- No third-party dependency chain to install or pin; the extraction is a single C extension.

What newspaper3k has that turbohtml does not
============================================

- URL downloading and caching. turbohtml takes parsed HTML; fetch the page yourself with ``urllib`` or ``httpx`` and
  pass the markup to :func:`turbohtml.parse`.
- Source-level article discovery (``newspaper.build(url)``, category and feed detection). No equivalent; drive your own
  crawl and call :meth:`~turbohtml.Node.article` per page.
- NLP: ``article.keywords`` and ``article.summary`` via ``nlp()``. No equivalent and out of scope; run an NLP library on
  ``article().text`` if you need it.
- ``authors`` as a full list of names. ``article().byline`` keeps only the first author source as one string; split it
  yourself if you need the individual names.
- Top-image and image-set extraction (``article.top_image``, ``article.images``). No equivalent; query the scored
  ``element`` for ``<img>`` yourself.

Performance
===========

Extracting the content body and metadata from a full page -- navigation, a scored article, and a footer.
:meth:`~turbohtml.Node.article` scores and harvests in one C pass over the parsed tree; newspaper3k builds an lxml tree
and runs its own regex-driven metadata scan. Numbers vary with input and hardware.

.. bench-table::
    :file: bench/newspaper3k.json

****************
 How to migrate
****************

Swap the download-then-parse dance for a fetch you own plus one :func:`turbohtml.parse` call. newspaper's ``Article``
combines both; turbohtml only does the parse and extract, so the network call is yours.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - newspaper3k
      - turbohtml
    - - ``Article(url); a.download(); a.parse()``
      - fetch yourself, then ``doc = parse(html)``
    - - ``article.text``
      - ``doc.article().text`` (or :meth:`~turbohtml.Node.main_text`)
    - - ``article.title``
      - ``doc.article().title``
    - - ``article.authors``
      - ``doc.article().byline``
    - - ``article.publish_date``
      - ``doc.article().date``
    - - ``article.meta_description``
      - ``doc.article().description``
    - - ``article.meta_lang``
      - ``doc.article().lang``
    - - ``article.top_node``
      - ``doc.article().element`` (the scored element, or ``None``)

.. testcode::

    doc = parse(
        "<html lang=en><head><title>Comets</title>"
        "<meta name=description content='A short guide to comets.'></head>"
        "<body><article class=post><h1>Comets</h1>"
        "<p>By <a rel=author href='/u'>Ada Lovelace</a></p>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "</article></body></html>"
    )
    art = doc.article()
    print(art.title, "|", art.byline, "|", art.description, "|", art.lang)

.. testoutput::

    Comets | Ada Lovelace | A short guide to comets. | en

**********************
 Gotchas and pitfalls
**********************

- newspaper3k downloads and caches URLs; :meth:`~turbohtml.Node.article` takes parsed HTML. Fetch the page yourself
  (``urllib`` or ``httpx``) and pass the markup to :func:`turbohtml.parse`.
- ``article().byline`` is a single whitespace-folded string from the first author source, where newspaper's
  ``article.authors`` is a list; split it yourself if you need the individual names.
- ``article().date`` is the harvested string; newspaper's ``publish_date`` is a ``datetime``. Parse it yourself if you
  need a date object.
- A page with no scoring article leaves ``element`` ``None`` and ``text`` empty while still filling the metadata, so
  branch on ``art.element`` rather than assuming a body.
- newspaper's keyword extraction, summarization, and other NLP have no turbohtml equivalent and are out of scope.
