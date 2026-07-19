#################
 From feedparser
#################

.. package-meta:: feedparser kurtmckee/feedparser

`feedparser <https://feedparser.readthedocs.io/>`_ is the canonical Python feed reader. ``feedparser.parse`` accepts a
URL, file, stream, or string, detects the format (RSS 0.9x-2.0, Atom 0.3/1.0, RDF/RSS-1.0, CDF), and returns a
``FeedParserDict`` whose ``feed`` and ``entries`` normalize every dialect's spelling of a field onto shared keys --
``title``, ``link``, ``id``, ``updated``/``published``, ``summary``/``content``, ``author`` -- with parsed ``*_parsed``
time structs, a ``bozo`` flag for malformed input, relative-URL resolution, and HTML sanitization of element content.

turbohtml serves the normalization core from :func:`turbohtml.extract.feed`, a string entry point over
:meth:`turbohtml.Document.feed`: the format is detected from the root element and each field mapped onto one frozen,
typed :class:`~turbohtml.extract.Feed` of :class:`~turbohtml.extract.Entry` records in one C walk of the parsed tree. It
keeps the field set and the precedence rules feedparser established, in the minimal typed shape ``htmlparser2``'s
``parseFeed`` models, and leaves the network, date-parsing, and content-sanitizing surface to the tools that own those
jobs.

*************************
 turbohtml vs feedparser
*************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - feedparser
    - - Scope
      - Full WHATWG parser; feed normalization is one feature of many
      - Single-purpose feed reader
    - - Feature breadth
      - Normalized ``Feed``/``Entry`` for RSS 2.0, Atom 1.0, and RDF/RSS-1.0 off the parsed tree
      - Every dialect back to RSS 0.9x and Atom 0.3, plus parsed dates, relative-URL resolution, content sanitizing, and
        network fetching
    - - Result shape
      - Frozen, fully typed :class:`~turbohtml.extract.Feed`/:class:`~turbohtml.extract.Entry` (``NamedTuple``)
      - ``FeedParserDict`` with attribute-or-key access and permissive fallbacks
    - - Performance
      - One C walk of the parsed tree; over 60x faster on a 30-item feed
      - Python SAX-style scanner with per-element handlers
    - - Dependencies
      - Zero runtime deps (self-contained C extension)
      - ``sgmllib3k``; optional ``chardet`` for byte streams
    - - Typing
      - Annotated records, ``py.typed``
      - Untyped ``FeedParserDict``

Feature overlap
===============

The shared surface you can port one-to-one:

- ``feedparser.parse(xml)`` -> :func:`turbohtml.extract.feed`, one call returning the normalized feed.
- ``result.feed.title`` / ``.link`` / ``.description`` -> :class:`~turbohtml.extract.Feed` ``.title``, ``.link``,
  ``.description``; ``result.feed.updated`` -> ``Feed.updated``.
- ``result.entries`` -> ``Feed.entries``, one :class:`~turbohtml.extract.Entry` per item.
- ``entry.title`` / ``.link`` / ``.id`` / ``.summary`` / ``.author`` -> the same names on
  :class:`~turbohtml.extract.Entry`; ``entry.published`` / ``.updated`` -> ``Entry.published`` / ``Entry.updated``;
  ``entry.content`` (feedparser's list) -> ``Entry.content`` (the resolved body string).

What turbohtml adds
===================

- A frozen, fully typed result: :class:`~turbohtml.extract.Feed` and :class:`~turbohtml.extract.Entry` are
  ``NamedTuple``\ s with ``py.typed`` coverage, where feedparser returns an untyped ``FeedParserDict``.
- One C walk under the per-tree critical section, over 60x faster than feedparser's Python scanner.
- Records that hold no reference back into the tree, so they outlive the document they came from.
- The rest of the read path on the same string: query, main-content, and structured-data extraction, so a feed and the
  pages it links are handled by one library.
- Zero third-party runtime dependencies: no ``sgmllib3k`` to install.

What feedparser has that turbohtml does not
===========================================

- **Parsed timestamps** (``published_parsed``, ``updated_parsed`` as ``time.struct_time``): no equivalent. turbohtml
  returns the timestamp string verbatim; parse it with :mod:`email.utils` (RSS RFC 822) or
  :meth:`datetime.datetime.fromisoformat` (Atom RFC 3339) yourself.
- **Older dialects** (RSS 0.9x, Atom 0.3, CDF) and their legacy element names: turbohtml targets RSS 2.0, Atom 1.0, and
  RDF/RSS-1.0.
- **Network and stream input** (``parse(url)`` / file / stream): no equivalent. turbohtml takes a decoded ``str``; fetch
  and decode with your own client first.
- **The ``bozo`` flag and relative-URL resolution**: turbohtml does not report a well-formedness bit and returns link
  values verbatim; resolve relatives with :func:`turbohtml.extract.normalize_url` when you have a base.
- **Content sanitizing**: feedparser strips unsafe markup from element content. turbohtml returns the text as-is; run it
  through :func:`turbohtml.clean.sanitize` if you will render it.

Performance
===========

Both start from the raw feed string and parse before they read it. On a 30-item RSS feed carrying titles, links, guids,
dates, ``dc:creator``, and ``content:encoded`` bodies, the C walk runs over 60 times faster than feedparser's scanner:

.. bench-table::
    :file: bench/feedparser.json

****************
 How to migrate
****************

Swap the import and the call; read attributes off the typed records instead of a ``FeedParserDict``.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `feedparser <https://feedparser.readthedocs.io/>`__
      - turbohtml
    - - ``feedparser.parse(xml)``
      - :func:`turbohtml.extract.feed`
    - - ``feedparser.parse(url)``
      - :func:`~turbohtml.extract.feed` on the body you fetch yourself
    - - ``result.feed.title`` / ``.link``
      - ``Feed.title`` / ``Feed.link``
    - - ``entry.published`` / ``entry.published_parsed``
      - ``Entry.published`` (string); parse it yourself for a ``datetime``
    - - ``entry.get("summary")``
      - ``Entry.summary``

Before, with ``feedparser``, the parse returns a ``FeedParserDict`` you read by key or attribute:

.. code-block:: python

    import feedparser

    result = feedparser.parse(
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Example Blog</title>"
        '<link href="https://blog.example/" rel="alternate"/>'
        "<entry><title>Hello, feeds</title>"
        '<link href="https://blog.example/hello" rel="alternate"/>'
        "<id>tag:blog.example,2026:hello</id>"
        "<author><name>A. Writer</name></author></entry></feed>"
    )
    print(result.feed.title)
    print(result.entries[0].title, result.entries[0].author)

After, :func:`~turbohtml.extract.feed` returns the same values from one C walk, as frozen typed records:

.. testcode::

    from turbohtml.extract import feed

    parsed = feed(
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Example Blog</title>"
        '<link href="https://blog.example/" rel="alternate"/>'
        "<updated>2026-07-07T09:00:00Z</updated>"
        "<entry><title>Hello, feeds</title>"
        '<link href="https://blog.example/hello" rel="alternate"/>'
        "<id>tag:blog.example,2026:hello</id>"
        "<published>2026-07-06T08:00:00Z</published>"
        "<summary>A first post.</summary>"
        "<author><name>A. Writer</name></author></entry></feed>"
    )
    print(parsed.type, parsed.title)
    entry = parsed.entries[0]
    print(entry.title, entry.author)
    print(entry.link)

.. testoutput::

    atom Example Blog
    Hello, feeds A. Writer
    https://blog.example/hello

turbohtml does not fetch URLs; ``feedparser.parse(url)`` becomes your HTTP client plus :func:`~turbohtml.extract.feed`
on the response body. A document with no feed root reads back as ``None`` rather than an empty ``FeedParserDict``:

.. testcode::

    print(feed("<html><body>not a feed</body></html>"))

.. testoutput::

    None

**********************
 Gotchas and pitfalls
**********************

- **Timestamps come back as strings**, not ``time.struct_time``. feedparser exposes both ``published`` (raw) and
  ``published_parsed`` (parsed); turbohtml returns only the raw string, so parse it yourself with :mod:`email.utils` for
  RSS or :meth:`datetime.datetime.fromisoformat` for Atom.
- **Absent fields are ``None``**, not a missing key. feedparser omits an absent key (``entry.get("summary")`` returns
  ``None``); :class:`~turbohtml.extract.Entry` always carries every field, set to ``None`` when the item lacks it, so
  read ``entry.summary`` directly.
- **A non-feed document returns ``None``**, where feedparser returns a ``FeedParserDict`` with ``bozo`` set and empty
  ``entries``. Guard the ``None`` before reading ``.entries``.
- **``content`` is one string**, the resolved body (``content:encoded`` or Atom ``<content>``), not feedparser's list of
  content dicts with ``type``/``value``. Read ``Entry.summary`` for the short form.
- **Link values are verbatim**, not resolved against the feed's base. Absolutize relative links yourself with
  :func:`turbohtml.extract.normalize_url` when you hold a base URL.
