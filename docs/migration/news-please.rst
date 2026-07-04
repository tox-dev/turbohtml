##################
 From news-please
##################

.. package-meta:: news-please fhamborg/news-please

`news-please <https://github.com/fhamborg/news-please>`_ is a news-crawling system. Scrapy spiders discover article
pages, an extractor ensemble (newspaper, readability, and a dedicated date extractor) pulls the body and metadata from
each page, and pipelines store the results in JSON, PostgreSQL, or Elasticsearch. It ships WARC and Common Crawl tooling
for large-scale news archiving and is used in academic corpus building and news-monitoring pipelines. Its in-library
entry point, ``NewsPlease.from_html``, runs the extraction ensemble on HTML you already hold.

turbohtml covers the extraction step of that stack. :meth:`~turbohtml.Node.article` scores the parsed tree and harvests
the article body and its metadata in one C pass, returning an :class:`~turbohtml.Article`. The crawling, scheduling, and
storage layers stay whatever you build them into; turbohtml is the ``from_html`` in the middle, without the ensemble's
dependency tree.

**************************
 turbohtml vs news-please
**************************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - news-please
    - - Scope
      - HTML parser plus a one-call article extractor (``article()``)
      - End-to-end news crawler: spiders, extraction ensemble, storage pipelines
    - - Feature breadth
      - Per-page body and metadata extraction from in-memory HTML
      - Crawling, scheduling, WARC/Common Crawl ingestion, DB/Elasticsearch sinks, plus extraction
    - - Performance
      - One C pass over the parsed tree
      - Several Python extractors whose votes are merged per page
    - - Typing
      - Typed :class:`~turbohtml.Article` NamedTuple, ships stubs
      - ``NewsArticle`` / dict result, no bundled type stubs
    - - Dependencies
      - Self-contained C extension
      - scrapy, newspaper, readability, date extractors, storage clients
    - - Maintenance
      - Active
      - Active, research-driven

Feature overlap
===============

The shared surface is single-page extraction. Everything ``NewsPlease.from_html`` returns for the body and core metadata
has a direct :meth:`~turbohtml.Node.article` counterpart:

- Article body text: ``maintext`` -> ``article().text``.
- Title: ``title`` -> ``article().title`` (from the first ``<h1>``, then ``og:title``, then ``<title>``).
- Author: ``authors`` -> ``article().byline`` (see the gotchas on the list-vs-string difference).
- Publication date: ``date_publish`` -> ``article().date``.
- Description: ``description`` -> ``article().description``.
- Language: ``language`` -> ``article().lang``.

What turbohtml adds
===================

- A full WHATWG HTML parser under the same object. ``parse(html)`` gives you the tree, CSS selectors, and serialization
  alongside ``article()``, so extraction and DOM work share one parse.
- A typed record. :class:`~turbohtml.Article` is a NamedTuple with stubs, so ``text``, ``title``, ``byline``, ``date``,
  ``description``, and ``lang`` are statically known; news-please returns a ``NewsArticle`` / dict.
- No ensemble dependency tree. The scoring and harvesting run in C with no scrapy, newspaper, or readability install.

What news-please has that turbohtml does not
============================================

- Crawling and scheduling. The scrapy spiders that discover and fetch article pages have no turbohtml equivalent; fetch
  pages yourself (``urllib``, ``httpx``) and pass the markup to ``parse``.
- WARC and Common Crawl ingestion (``from_warc`` and the Common Crawl tooling). No equivalent; read WARC records with a
  library such as ``warcio`` and hand each record's HTML to ``parse``.
- Storage pipelines (JSON files, PostgreSQL, Elasticsearch). No equivalent; write the ``Article`` fields to your own
  sink.
- An extractor ensemble that merges several votes per page. turbohtml runs a single scoring pass. news-please's merge
  can recover fields a single extractor misses, at the cost of running multiple Python extractors.
- Inferred publication dates. news-please's date extractor derives a ``datetime`` even when the page declares none;
  ``article().date`` reports only the declared string. Keep a date-inference library for pages that declare no date.

Performance
===========

Extracting from a full page -- navigation, a scored article, and a footer. :meth:`~turbohtml.Node.article` scores and
harvests in one C pass; news-please runs several Python extractors and merges their votes. Numbers vary with input and
hardware.

.. bench-table::
    :file: bench/news-please.json

****************
 How to migrate
****************

Drop the ``NewsPlease`` import for ``parse``, and fetch pages yourself where you previously called ``from_url``:

.. code-block:: python

    from turbohtml import parse

Map the calls:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `news-please <https://github.com/fhamborg/news-please>`__
      - turbohtml
    - - ``NewsPlease.from_html(html, url=...)``
      - ``parse(html).article()``
    - - ``article.title``
      - ``article().title``
    - - ``article.maintext``
      - ``article().text``
    - - ``article.authors`` (a list)
      - ``article().byline`` (the first declared author, one string)
    - - ``article.date_publish`` (a ``datetime``)
      - ``article().date`` (the declared string; parse with ``datetime.fromisoformat`` or ``dateutil``)
    - - ``article.description``
      - ``article().description``
    - - ``article.language``
      - ``article().lang``
    - - ``NewsPlease.from_url(url)`` / ``from_urls``
      - fetch the page yourself (``urllib`` or ``httpx``), then parse the markup

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
    print(art.title, "|", art.byline, "|", art.date, "|", art.lang)

.. testoutput::

    Comets | Ada Lovelace | 2024-05-06 | en

**********************
 Gotchas and pitfalls
**********************

- news-please is a crawler; turbohtml replaces only the per-page extraction. The spider configuration, WARC ingestion
  (``from_warc``, the Common Crawl tooling), and the storage pipelines have no turbohtml equivalent.
- ``article().byline`` is one string from the first declared author source; news-please's ``authors`` merges every name
  its extractors find, which on wire-service pages includes agencies ("the Associated Press") beside the byline.
- ``date_publish`` is a ``datetime`` inferred by a dedicated extractor; ``article().date`` reports the page's declared
  value as a string and infers nothing, so parse it yourself and keep a date-inference library for pages that declare
  none.
- ``article.language`` is a normalized two-letter code; ``article().lang`` reports the ``<html lang>`` attribute
  verbatim (``en-us`` stays ``en-us``).
