##########################
 Read an RSS or Atom feed
##########################

Pull the items out of a syndication feed with :func:`turbohtml.extract.feed`, the ``feedparser`` successor. It detects
the format -- RSS 2.0, Atom 1.0, or RDF/RSS 1.0 -- from the root element and normalizes every item into one shape, so
the same code reads an Atom ``<entry>`` and an RSS ``<item>``:

.. testcode::

    from turbohtml.extract import feed

    parsed = feed(
        '<rss version="2.0"><channel>'
        "<title>Widgets Weekly</title><link>https://widgets.example/</link>"
        "<item><title>Cogs ship Tuesday</title><link>https://widgets.example/cogs</link>"
        "<guid>tag:widgets,2026:cogs</guid>"
        "<pubDate>Tue, 07 Jul 2026 09:00:00 GMT</pubDate>"
        "<description>The cogs are ready.</description></item>"
        "</channel></rss>"
    )
    print(parsed.type, parsed.title)
    entry = parsed.entries[0]
    print(entry.title, "->", entry.link)
    print(entry.published)

.. testoutput::

    rss Widgets Weekly
    Cogs ship Tuesday -> https://widgets.example/cogs
    Tue, 07 Jul 2026 09:00:00 GMT

A :class:`~turbohtml.extract.Feed` carries the feed's ``type``, ``title``, ``link``, ``description``, and ``updated``
plus its ``entries``, each an :class:`~turbohtml.extract.Entry` whose ``title``, ``link``, ``id``,
``updated``/``published``, ``summary``/``content``, and ``author`` are the first present value across the format's
spellings. Timestamps come back verbatim, so parse them with your own date library when you need a ``datetime``.

The same call reads an Atom feed without a change in the reading code, because the field names are normalized:

.. testcode::

    parsed = feed(
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Widgets Weekly</title>"
        "<entry><title>Gears in stock</title>"
        '<link href="https://widgets.example/gears"/>'
        "<id>tag:widgets,2026:gears</id>"
        "<updated>2026-07-07T09:00:00Z</updated></entry></feed>"
    )
    print(parsed.type)
    print(parsed.entries[0].title, "->", parsed.entries[0].link)

.. testoutput::

    atom
    Gears in stock -> https://widgets.example/gears

:func:`~turbohtml.extract.feed` returns ``None`` for a document with no ``<rss>``, ``<feed>``, or ``<rdf:RDF>`` root, so
guard the result. The :doc:`feedparser migration guide </migration/feedparser>` maps the field names.
