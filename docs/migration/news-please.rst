##################
 From news-please
##################

.. package-meta:: news-please fhamborg/news-please

`news-please <https://github.com/fhamborg/news-please>`_ is a news-crawling system: scrapy spiders discover pages, an
extractor ensemble (newspaper, readability, a date extractor) pulls the article and metadata from each, and pipelines
store the results in JSON, PostgreSQL, or Elasticsearch. Its in-library entry point, ``NewsPlease.from_html``, runs that
ensemble on HTML you already have.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Node.article` replaces the extraction step: one C pass over the parsed tree scores the content body
and harvests the metadata the ensemble votes on, returning the :class:`~turbohtml.Article` record (``text``, ``title``,
``byline``, ``date``, ``description``, ``lang``). The crawling, scheduling, and storage layers stay whatever you make
them; turbohtml is the ``from_html`` in the middle, without the ensemble's dependency tree.

Extracting from a full page -- navigation, a scored article, and a footer. :meth:`~turbohtml.Node.article` scores and
harvests in one C pass; news-please runs several Python extractors and merges their votes. Numbers vary with input and
hardware.

.. bench-table::
    :file: bench/news-please.json

*************
 The renames
*************

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

**********
 Pitfalls
**********

- news-please is a crawler; turbohtml replaces only the per-page extraction. The spider configuration, WARC ingestion
  (``from_warc``, the Common Crawl tooling), and the storage pipelines have no turbohtml equivalent.
- ``article().byline`` is one string from the first declared author source; news-please's ``authors`` merges every name
  its extractors find, which on wire-service pages includes agencies ("the Associated Press") beside the byline.
- ``date_publish`` is a ``datetime`` inferred by a dedicated extractor; ``article().date`` reports the page's declared
  value as a string and infers nothing, so parse it yourself and keep a date-inference library for pages that declare
  none.
- ``article.language`` is a normalized two-letter code; ``article().lang`` reports the ``<html lang>`` attribute
  verbatim (``en-us`` stays ``en-us``).
