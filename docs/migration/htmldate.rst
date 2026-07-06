###############
 From htmldate
###############

.. package-meta:: htmldate adbar/htmldate

`htmldate <https://htmldate.readthedocs.io/>`_ is the standalone publication-date finder that sits underneath
``trafilatura``. Its one entry point, ``find_date(html)``, reads a page's ``<meta>`` tags, JSON-LD, ``<time>`` elements,
canonical URL, and visible text, then returns the date as a formatted string. It leans on ``lxml`` for parsing and an
optional ``dateparser`` fallback for free-form and multilingual phrasings, which makes it the go-to date extractor for
news scraping and corpus building in the trafilatura ecosystem.

:func:`turbohtml.extract.dates` covers the same ground off the already-parsed WHATWG tree. It scores the identical
signals in htmldate's stage-priority order, carries htmldate's knobs on one frozen
:class:`~turbohtml.extract.DateExtraction` config, and returns a :class:`~turbohtml.extract.PublicationDate` that names
which signal the date came from instead of a bare string. No second parse, no ``lxml`` or ``dateparser`` dependency.

***********************
 turbohtml vs htmldate
***********************

.. list-table::
    :header-rows: 1
    :widths: 18 41 41

    - - Dimension
      - turbohtml
      - htmldate
    - - Scope
      - Full WHATWG HTML5 parser with a date engine as one extractor among many (metadata, links, text, structured data)
      - Single-purpose publication-date finder over an ``lxml`` tree
    - - Feature breadth
      - Same five signals (meta, JSON-LD, time, URL, text) scored off the DOM; standard-library date parsing
      - Same five signals plus boilerplate pruning, occurrence scoring, and ``dateparser`` for wide locale coverage
    - - Performance
      - 2x to 2.6x faster on boilerplate pages with no date metadata (early exit over structured signals)
      - Edges ahead on clean metadata pages where the header lookup returns first
    - - Typing
      - Fully typed; returns a :class:`~turbohtml.extract.PublicationDate` NamedTuple or ``None``
      - Typed hints; returns ``str`` or ``None``
    - - Dependencies
      - No third-party runtime dependencies (bundled C extension)
      - ``lxml``, ``charset_normalizer``, ``dateutil``, ``urllib3``; ``dateparser`` optional
    - - Maintenance
      - Active, part of turbohtml
      - Active, maintained by Adrien Barbaresi alongside trafilatura

Feature overlap
===============

The date-finding surface ports one-to-one:

- ``find_date(html)`` maps to :func:`turbohtml.extract.dates`, reading the same signals in the same priority order.
- ``original_date`` maps to ``DateExtraction(original=True)``: prefer the first-published date over the last-modified
  one.
- ``outputformat`` maps to ``DateExtraction(output_format=...)``, an :meth:`~datetime.date.strftime` string.
- ``min_date`` / ``max_date`` map to ``DateExtraction(min_date=..., max_date=...)``.
- ``extensive_search`` maps to ``DateExtraction(extensive_search=...)``, gating the visible-text scan.
- The ``<meta>``, JSON-LD ``datePublished`` / ``dateModified``, ``<time>``, and canonical-URL vocabularies are drawn
  from htmldate's own key lists, so the same tags win on both.

What turbohtml adds
===================

- **Signal provenance.** The result names which engine won (``"meta"``, ``"json-ld"``, ``"time"``, ``"url"``,
  ``"text"``); htmldate returns only the string, so you cannot tell a hard metadata date from a text guess.
- **No second parse.** The date is read off the tree turbohtml already built, not a fresh ``lxml`` parse of the markup.
- **One extractor of many.** The same parsed document feeds turbohtml's metadata, link, text, and structured-data
  extractors; htmldate does dates only.
- **No third-party runtime dependencies.** htmldate pulls in ``lxml``, ``charset_normalizer``, ``dateutil``, and
  ``urllib3``.

.. _htmldate-divergences:

What htmldate has that turbohtml does not
=========================================

- **Boilerplate pruning.** htmldate deletes comments, navigation, and footers before scanning text, then picks the most
  frequent plausible date. turbohtml scores the structured signals first and falls back to the modal date in visible
  text without pruning the tree. Workaround: strip boilerplate yourself before calling ``dates`` when a page buries its
  real date among navigation timestamps.
- **``dateparser`` locale reach.** htmldate parses free-form and wide-locale date phrases through ``dateparser``.
  turbohtml uses standard-library parsing: ISO 8601, common numeric spellings, an 8-digit stamp, and a compact
  English/German/French/Spanish/Italian month vocabulary. No equivalent for languages or phrasings outside that set.
- **``url=`` parameter.** htmldate accepts an explicit ``url`` argument as a date signal. turbohtml has no such
  parameter; it reads the date from the markup's own ``<link rel=canonical>`` or ``og:url``. Workaround: pass markup
  that carries the canonical link.
- **String bounds.** htmldate's ``min_date`` / ``max_date`` also accept ISO strings. turbohtml requires
  :class:`datetime.date` objects.

Performance
===========

Both libraries are parse-bound. On clean metadata pages htmldate's header lookup returns first and edges ahead; on
boilerplate pages with no date metadata turbohtml's early exit over the structured signals runs 2x to 2.6x faster than
htmldate's tree-pruning text scoring:

.. bench-table::
    :file: bench/htmldate.json

Over the 200 real-world news pages in htmldate's ``mediacloud`` evaluation set the two agree on 91% of inputs, and
turbohtml matches the gold date on 85% (htmldate 89%). On htmldate's 55 hand-picked edge-case pages -- blogs and sparse
pages tuned into htmldate's own suite -- turbohtml trails (69% against 96%): those need the boilerplate pruning and
occurrence scoring called out under `What htmldate has that turbohtml does not`_.

****************
 How to migrate
****************

Swap the import and the call. The keyword arguments htmldate spreads across ``find_date`` live on one immutable config,
and the date carries its provenance:

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

The default reads the most recent date the page reports; ``original=True`` returns the first-published one:

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

Text scanning and bounds behave as htmldate's do, and an out-of-window date returns ``None``:

.. testcode::

    from datetime import date

    from turbohtml.extract import DateExtraction, dates

    print(dates("<body><p>Posted July 4, 2016 by staff.</p></body>"))
    print(dates("<meta name=date content=2010-01-01>", DateExtraction(min_date=date(2015, 1, 1))))

.. testoutput::

    PublicationDate(date='2016-07-04', signal='text')
    None

**********************
 Gotchas and pitfalls
**********************

- The default prefers the *modification* date (the most recent the page reports), matching htmldate's default. Pass
  ``DateExtraction(original=True)`` for the first-published date.
- :func:`~turbohtml.extract.dates` returns ``None`` or a :class:`~turbohtml.extract.PublicationDate`, never a bare
  string. Use ``result.date`` where htmldate code expects the ``str``, guarding the ``None`` first.
- There is no ``url=`` parameter: the date is read from the markup's own ``<link rel=canonical>`` or ``og:url``. Pass a
  page that carries the canonical link when the URL is the only date signal.
- ``extensive_search`` is on by default, as in htmldate; turn it off to read only the structured signals and never scan
  visible text.
- ``min_date`` / ``max_date`` must be :class:`datetime.date` objects, defaulting to 1995-01-01 and today. htmldate also
  accepts ISO strings; convert them with :meth:`datetime.date.fromisoformat` first.
- Dates outside turbohtml's month vocabulary (locales beyond English, German, French, Spanish, Italian) fall through the
  text stage. Rely on the structured signals for those pages, or keep htmldate's ``dateparser`` path for the tail.
