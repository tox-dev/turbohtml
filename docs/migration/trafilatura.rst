##################
 From trafilatura
##################

.. image:: https://static.pepy.tech/badge/trafilatura/month
    :alt: trafilatura monthly downloads
    :target: https://pepy.tech/project/trafilatura

`trafilatura <https://trafilatura.readthedocs.io>`_ extracts the main text and metadata from a web page: it downloads
the URL, scores the content body, and returns the article alongside its title, author, date, and description. It also
layers in optional language detection and precision-tuned publication-date inference, the latter from the `htmldate
<https://htmldate.readthedocs.io>`_ library it builds on.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Node.article` answers the same question -- *given a cluttered page, what is the article and its
metadata* -- in one C pass over the parsed tree. It returns an :class:`~turbohtml.Article` record (``element``,
``text``, ``title``, ``byline``, ``date``, ``description``, ``lang``), harvesting the *declared* metadata rather than
inferring it. turbohtml works on HTML you already have, so pair it with a downloader (``urllib`` or ``httpx``) and, when
you need them, trafilatura's heavier language and date heuristics.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - trafilatura
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

Extracting the content body and metadata from a full page -- navigation, a scored article, and a footer -- measured with
``tox -e bench article`` on CPython 3.14 (release build, Apple M4, macOS 26). :meth:`~turbohtml.Node.article` scores and
harvests in one C pass over the parsed tree; trafilatura builds an lxml tree in Python first. Numbers vary with input
and hardware.

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - input
      - turbohtml
      - trafilatura
      - speed-up
    - - post (4 KiB)
      - 23 µs
      - 1.34 ms
      - 58x
    - - longform (16 KiB)
      - 70 µs
      - 3.13 ms
      - 45x

******************************
 Publication dates (htmldate)
******************************

trafilatura's date support comes from `htmldate <https://htmldate.readthedocs.io>`_, whose ``find_date(html)`` scans
many patterns and validates the result against a range. ``doc.article().date`` is the lightweight counterpart: it
returns the first of a ``<time>``, an ``article:published_time`` meta, and a common date meta (``date``, ``pubdate``,
``dc.date``) exactly as the page declares it, without parsing or normalizing the value. Wrap it in
``datetime.date.fromisoformat`` or ``dateutil`` when you need a real date object, and keep htmldate for pages whose date
is only inferable.

**********
 Pitfalls
**********

- trafilatura downloads URLs; :meth:`~turbohtml.Node.article` takes parsed HTML. Fetch the page yourself (``urllib`` or
  ``httpx``) and pass the markup to :func:`turbohtml.parse`.
- ``article().lang`` reports the document's ``<html lang>`` attribute, not a language *detected* from the prose the way
  trafilatura's optional language filter does; that inference is out of scope.
- A page with no scoring article leaves ``element`` ``None`` and ``text`` empty while still filling the metadata, so
  branch on ``art.element`` rather than assuming a body.
