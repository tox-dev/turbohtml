##################
 From trafilatura
##################

.. package-meta:: trafilatura adbar/trafilatura

`trafilatura <https://trafilatura.readthedocs.io>`_ extracts the main text and metadata from a web page: it downloads
the URL, scores the content body against navigation and boilerplate, and returns the article alongside its title,
author, date, description, and site name. It serializes to plain text, Markdown, XML/TEI, JSON, or CSV, and layers in
optional comment extraction, table and link handling, deduplication across a crawl, language detection, and
precision-tuned publication-date inference (the last from the `htmldate <https://htmldate.readthedocs.io>`_ library it
builds on). It is a common front end for building text corpora for NLP and search indexing.

turbohtml covers the extraction core of that: :meth:`~turbohtml.Node.article` scores a parsed page and harvests its
*declared* metadata in one C pass, returning an :class:`~turbohtml.Article` record. It works on HTML you already have,
so pair it with your own downloader and, when you need trafilatura's heavier heuristics (inferred dates, Markdown/XML
output), keep those alongside it. Detected language it now matches: :func:`turbohtml.detect.detect_language` classifies
the extracted text.

**************************
 turbohtml vs trafilatura
**************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - trafilatura
    - - Scope
      - Full WHATWG HTML parser with DOM, query, serialize, and content extraction on top
      - Content-and-metadata extraction plus fetching, crawling, and multi-format output
    - - Feature breadth
      - One C scoring pass yielding a body element and declared metadata (:class:`~turbohtml.Article`)
      - Extraction with comments, tables, links, images, dedup, language ID, and inferred dates
    - - Performance
      - C scoring pass over the parsed tree (see below)
      - Python scoring over an lxml tree, with optional readability/justext fallbacks
    - - Typing
      - Fully annotated, ships PEP 561 stubs
      - Annotated pure-Python source
    - - Dependencies
      - Compiled C extension
      - lxml plus courlan, htmldate, and charset-normalizer
    - - Maintenance
      - Actively developed
      - Actively developed and widely used

Feature overlap
===============

The shared surface ports one call to one call:

- ``trafilatura.extract(html)`` -> ``parse(html).article().text`` (or :meth:`~turbohtml.Node.main_text`), the scored
  body as plain text.
- ``extract_metadata(html).title`` -> ``article().title``.
- ``extract_metadata(html).author`` -> ``article().byline``.
- ``extract_metadata(html).date`` -> ``article().date`` (as declared, see the date pitfall below).
- ``extract_metadata(html).description`` -> ``article().description``.
- the extracted content body -> ``article().element``, the scored element (:attr:`~turbohtml.Node.html` for its markup),
  or ``None`` when nothing reads as content.

What turbohtml adds
===================

- The extraction rides on a full WHATWG parse, so the scored body comes back as a DOM element you can query, mutate, and
  serialize, not only as text. :meth:`~turbohtml.Node.main_content` returns that element directly.
- Metadata is harvested from what the page *declares* (``<html lang>``, ``article:published_time``, ``rel=author``,
  ``og:*``, ``<title>``) in the same C pass as the body, with no second Python analysis stage.
- :func:`turbohtml.parse` follows the WHATWG recovery rules and never raises on malformed markup, and the parsed tree is
  reusable for anything else you need from the page.

What trafilatura has that turbohtml does not
============================================

- Fetching and crawling: ``fetch_url(url)``, ``fetch_response``, plus sitemap, feed, and spider helpers. turbohtml has
  no fetcher; read the page with ``urllib`` or ``httpx`` and pass the markup to :func:`~turbohtml.parse`.
- Output formats. ``extract(html, output_format="markdown" | "xml" | "xmltei" | "json" | "csv")`` serializes the result;
  turbohtml returns plain text and the DOM element, so build Markdown or JSON from ``article()`` yourself.
- Comment, table, link, and image extraction toggles (``include_comments``, ``include_tables``, ``include_links``,
  ``include_images``). turbohtml scores one prose body; extract those regions from the DOM by hand.
- ``favor_precision`` / ``favor_recall`` tuning and the readability-lxml / justext fallbacks. turbohtml has a single
  scoring model with no drop-in aggressiveness switch.
- Cross-document deduplication (the LRU cache that drops repeated segments across a crawl). No equivalent.
- Inferred publication dates via htmldate. ``article().date`` returns the declared date string and does not infer; keep
  htmldate for pages where the date is only inferable. Prose language, by contrast, turbohtml now infers:
  :func:`turbohtml.detect.detect_language` classifies the extracted text (``article().lang`` still only reports the
  declared ``<html lang>``).
- License and hostname metadata fields. :meth:`~turbohtml.Node.article` exposes ``title``, ``byline``, ``date``,
  ``description``, ``lang``, ``canonical``, ``site_name``, ``tags``, and ``image`` (the lead image); derive the hostname
  from ``canonical`` with :func:`urllib.parse.urlsplit` and read a license ``<link rel="license">`` with a selector.

Performance
===========

:meth:`~turbohtml.Node.article` scores and harvests in one C pass over the parsed tree; trafilatura builds an lxml tree
in Python first and scores it there. Numbers vary with input and hardware.

.. bench-table::
    :file: bench/trafilatura.json

****************
 How to migrate
****************

Swap the trafilatura import for :func:`turbohtml.parse` and read the fields off one :meth:`~turbohtml.Node.article`
call:

.. code-block:: python

    from turbohtml import parse

The call mapping:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `trafilatura <https://trafilatura.readthedocs.io/>`__
      - turbohtml
    - - ``trafilatura.extract(html)``
      - ``doc.article().text`` (or :meth:`~turbohtml.Node.main_text`)
    - - ``extract_metadata(html).title``
      - ``doc.article().title``
    - - ``extract_metadata(html).author``
      - ``doc.article().byline``
    - - ``extract_metadata(html).date``
      - ``doc.article().date``
    - - ``extract_metadata(html).description``
      - ``doc.article().description``
    - - the extracted content body
      - ``doc.article().element`` (the scored element; :attr:`~turbohtml.Node.html` for its markup, or ``None``)
    - - ``trafilatura.fetch_url(url)``
      - fetch the page yourself (``urllib`` or ``httpx``), then parse the markup

Before and after, harvesting the body and metadata from a full page:

.. testcode::

    doc = parse(
        "<html lang=en><head><title>Comets</title>"
        "<meta property=article:published_time content='2024-05-06'></head>"
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

- trafilatura downloads URLs; :meth:`~turbohtml.Node.article` takes parsed HTML. Fetch the page yourself (``urllib`` or
  ``httpx``) and pass the markup to :func:`turbohtml.parse`.
- ``article().date`` returns the date exactly as the page declares it (the first of a ``<time>``, an
  ``article:published_time`` meta, or a common date meta such as ``date``, ``pubdate``, ``dc.date``) without parsing or
  normalizing it. Wrap it in ``datetime.date.fromisoformat`` or ``dateutil`` for a real date object, and keep htmldate
  for pages whose date is only inferable.
- ``article().lang`` reports the document's declared ``<html lang>`` attribute, not a language detected from the prose.
  To infer the language the way trafilatura's optional language filter does, pass the extracted ``text`` to
  :func:`turbohtml.detect.detect_language`, which returns an ISO 639-3 code with a confidence.
- A page with no scoring article leaves ``element`` ``None`` and ``text`` empty while still filling the metadata, so
  branch on ``art.element`` rather than assuming a body.
- turbohtml returns plain text only. For the Markdown, XML, or JSON that ``extract(..., output_format=...)`` produces,
  serialize the ``article()`` fields (and the ``element`` subtree) yourself.
