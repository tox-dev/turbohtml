###############
 From htmldate
###############

.. package-meta:: htmldate adbar/htmldate

`htmldate <https://htmldate.readthedocs.io/>`_ is the standalone publication-date finder underneath ``trafilatura``:
``find_date(html)`` reads a page's ``<meta>`` tags, JSON-LD, ``<time>`` elements, URL, and visible text and returns the
date as a string. :func:`turbohtml.extract.dates` covers that surface off the parsed DOM, with the knobs on one frozen
:class:`~turbohtml.extract.DateExtraction` config.

***************
 Why turbohtml
***************

turbohtml reads the same signals htmldate reads, but over the WHATWG tree and the
:meth:`~turbohtml.Document.structured_data` engine it already builds, so no second parse and no ``dateparser`` /
``lxml`` dependency is pulled in. The result is a :class:`~turbohtml.extract.PublicationDate` that names the signal the
date came from, not a bare string:

.. testcode::

    from turbohtml.extract import DateExtraction, dates

    page = (
        '<meta property="article:published_time" content="2016-12-23T10:00:00Z">'
        '<meta property="article:modified_time" content="2017-02-01">'
    )
    print(dates(page))
    print(dates(page, DateExtraction(original=True)))

.. testoutput::

    PublicationDate(date='2017-02-01', signal='meta')
    PublicationDate(date='2016-12-23', signal='meta')

Both libraries are parse-bound. On clean metadata pages htmldate's header lookup returns first and edges ahead; on
boilerplate pages with no date metadata turbohtml's early exit over the structured signals runs 2x-4x faster than
htmldate's tree-pruning text scoring:

.. bench-table::
    :file: bench/htmldate.json

Over the 200 real-world news pages in htmldate's ``mediacloud`` evaluation set the two agree on 91% of inputs, and
turbohtml matches the gold date on 85% (htmldate 89%). On htmldate's 55 hand-picked edge-case pages -- blogs and sparse
pages tuned into htmldate's own suite -- turbohtml trails (69% against 96%): those need the boilerplate pruning and
occurrence scoring called out under :ref:`htmldate-divergences`.

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `htmldate <https://htmldate.readthedocs.io/>`__
      - turbohtml
    - - ``htmldate.find_date(html)``
      - :func:`turbohtml.extract.dates` (returns a :class:`~turbohtml.extract.PublicationDate`, not a ``str``)
    - - ``find_date(html, original_date=True)``
      - ``dates(html, DateExtraction(original=True))``
    - - ``find_date(html, outputformat="%d %B %Y")``
      - ``dates(html, DateExtraction(output_format="%d %B %Y"))``
    - - ``find_date(html, min_date=..., max_date=...)``
      - ``DateExtraction(min_date=..., max_date=...)`` (a :class:`datetime.date`)
    - - ``find_date(html, extensive_search=False)``
      - ``DateExtraction(extensive_search=False)``
    - - ``find_date(html, url=...)``
      - reads the page's own ``<link rel=canonical>`` / ``og:url``; pass markup that carries it

The keyword arguments htmldate spreads across ``find_date`` live on one immutable config, and the date carries its
provenance:

.. testcode::

    from datetime import date

    from turbohtml.extract import DateExtraction, dates

    print(dates("<body><p>Posted July 4, 2016 by staff.</p></body>"))
    print(dates("<meta name=date content=2010-01-01>", DateExtraction(min_date=date(2015, 1, 1))))

.. testoutput::

    PublicationDate(date='2016-07-04', signal='text')
    None

.. _htmldate-divergences:

**************************************
 Deliberate divergences and omissions
**************************************

turbohtml scores the structured date signals off the DOM; htmldate layers heavier heuristics on top, and skipping them
is where the two part ways on sparse pages:

- **No boilerplate pruning.** htmldate deletes comments, navigation, and footers before scanning text, then picks the
  most frequent plausible date. turbohtml scores the structured signals (meta, JSON-LD, time, URL) first and only falls
  back to the modal date in the visible text, without pruning the tree.
- **No ``dateparser`` fallback.** Date strings are parsed with the standard library: ISO 8601, the common numeric
  spellings, an 8-digit stamp, and a compact English/German/French/Spanish/Italian month vocabulary. htmldate's
  ``dateparser`` reaches more locales and free-form phrasings.
- **A date, with its source.** :func:`~turbohtml.extract.dates` returns which signal won (``"meta"``, ``"json-ld"``,
  ``"time"``, ``"url"``, ``"text"``); htmldate returns only the string. Read ``.date`` for the drop-in value.
- **Bounds are dates.** ``min_date`` / ``max_date`` are :class:`datetime.date` objects, defaulting to 1995-01-01 and
  today; htmldate also accepts ISO strings.

**********
 Pitfalls
**********

- The default prefers the *modification* date (the most recent the page reports), matching htmldate's default. Pass
  ``DateExtraction(original=True)`` for the first-published date.
- :func:`~turbohtml.extract.dates` returns ``None`` or a :class:`~turbohtml.extract.PublicationDate`, never a bare
  string. Use ``result.date`` where htmldate code expects the ``str``, guarding the ``None`` first.
- There is no ``url=`` parameter: the date is read from the markup's own ``<link rel=canonical>`` or ``og:url``. Pass a
  page that carries the canonical link when the URL is the only date signal.
- ``extensive_search`` is on by default, as in htmldate; turn it off to read only the structured signals and never scan
  visible text.
