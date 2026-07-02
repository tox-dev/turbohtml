#############
 From goose3
#############

.. package-meta:: goose3 goose3/goose3

`goose3 <https://goose3.readthedocs.io>`_ is an article extractor in the newspaper mold: ``Goose().extract(url=...)``
(or ``raw_html=...``) fetches a page, scores the content body on an lxml tree, runs its cleaners over the winner, and
returns an ``Article`` carrying the cleaned text alongside the harvested metadata, images, and embedded media.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Node.article` fills the same contract in one C pass over the parsed tree: the content-density scoring
picks the body and the metadata harvest fills the :class:`~turbohtml.Article` record (``element``, ``text``, ``title``,
``byline``, ``date``, ``description``, ``lang``) from the page's declared sources. turbohtml works on HTML you already
have, so pair it with a downloader (``urllib`` or ``httpx``) where goose fetches for you.

Extracting the content body and metadata from a full page -- navigation, a scored article, and a footer.
:meth:`~turbohtml.Node.article` scores and harvests in one C pass over the parsed tree; goose3 builds an lxml tree,
scores it, and runs its cleaner chain in Python. Numbers vary with input and hardware.

.. bench-table::
    :file: bench/goose3.json

*************
 The renames
*************

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

**********
 Pitfalls
**********

- goose3 downloads URLs (``extract(url=...)``); :meth:`~turbohtml.Node.article` takes parsed HTML. Fetch the page
  yourself and pass the markup to :func:`turbohtml.parse`.
- ``article().byline`` is one whitespace-folded string from the first author source; goose's ``authors`` is a list, so a
  multi-author page keeps only the first name.
- Both report the date the page declares, from different preferred sources (a ``<time>`` first for turbohtml,
  ``article:published_time`` first for goose), so the same page can answer with two spellings of the same instant.
- ``article().title`` prefers the first ``<h1>``, then ``og:title``, then ``<title>``; goose starts from ``<title>`` and
  splits site-name suffixes off, so pages whose ``<h1>`` and ``<title>`` disagree yield different titles.
- goose's image (``top_image``, ``infography``) and embed (``movies``, ``tweets``) extraction, its language-specific
  stopword configuration, and video handling have no turbohtml equivalent and are out of scope.
