##################
 From newspaper3k
##################

.. image:: https://static.pepy.tech/badge/newspaper3k/month
    :alt: newspaper3k monthly downloads
    :target: https://pepy.tech/project/newspaper3k

`newspaper3k <https://newspaper.readthedocs.io>`_ is a news-article scraper: an ``Article`` downloads a URL, then
``parse()`` fills ``article.text``, ``article.title``, ``article.authors``, ``article.publish_date`` and the rest from
the page, with optional NLP on top.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Node.article` returns the same shape in one C pass: an :class:`~turbohtml.Article` record with the
scored ``element`` and its ``text``, plus the harvested ``title``, ``byline``, ``date``, ``description`` and ``lang``.
turbohtml does not fetch URLs, so download the page yourself and parse the markup; the keyword and summary NLP newspaper
bundles has no equivalent and is out of scope.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - newspaper3k
      - turbohtml
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

Extracting the content body and metadata from a full page -- navigation, a scored article, and a footer -- measured with
``tox -e bench article`` on CPython 3.14 (release build, Apple M4, macOS 26). :meth:`~turbohtml.Node.article` scores and
harvests in one C pass over the parsed tree; newspaper3k builds an lxml tree and runs its own regex-driven metadata
scan. Numbers vary with input and hardware.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - input
      - turbohtml
      - newspaper3k
      - speed-up
    - - post (4 KiB)
      - 23 µs
      - 3.52 ms
      - 152x
    - - longform (16 KiB)
      - 70 µs
      - 8.97 ms
      - 128x

**********
 Pitfalls
**********

- newspaper3k downloads and caches URLs; :meth:`~turbohtml.Node.article` takes parsed HTML. Fetch the page yourself
  (``urllib`` or ``httpx``) and pass the markup to :func:`turbohtml.parse`.
- ``article().byline`` is a single whitespace-folded string from the first author source, where newspaper's
  ``article.authors`` is a list; split it yourself if you need the individual names.
- newspaper's keyword extraction, summarization, and other NLP have no turbohtml equivalent and are out of scope.
- A page with no scoring article leaves ``element`` ``None`` and ``text`` empty while still filling the metadata, so
  branch on ``art.element`` rather than assuming a body.
