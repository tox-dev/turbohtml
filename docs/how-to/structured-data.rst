#########################
 Extract structured data
#########################

Pull the machine-readable metadata a page embeds -- JSON-LD, Microdata, OpenGraph and Twitter cards, RDFa -- with
:meth:`turbohtml.Document.structured_data`, the ``extruct`` successor.

****************************************
 Pull every embedded format in one call
****************************************

Scrapers want the JSON-LD, Microdata, and OpenGraph/Twitter metadata a page embeds, the job of ``extruct`` or
``metadata_parser``. :meth:`~turbohtml.Document.structured_data` pulls every supported format in one walk:

.. testcode::

    doc = turbohtml.parse(
        '<head><meta property="og:title" content="Widgets"></head>'
        '<body><script type="application/ld+json">{"@type": "Product", "name": "Widget"}</script>'
        '<div itemscope itemtype="https://schema.org/Offer"><span itemprop="price">9.99</span></div></body>'
    )
    data = doc.structured_data()
    print(data.json_ld)
    print(data.opengraph)
    item = data.microdata[0]
    print(item.type, item.properties)

.. testoutput::

    [{'@type': 'Product', 'name': 'Widget'}]
    {'og:title': 'Widgets'}
    https://schema.org/Offer {'price': ['9.99']}

:meth:`~turbohtml.Document.structured_data` returns a :class:`~turbohtml.StructuredData` record whose fields you read by
attribute. The per-format helpers :meth:`~turbohtml.Document.json_ld`, :meth:`~turbohtml.Document.opengraph`,
:meth:`~turbohtml.Document.microdata`, :meth:`~turbohtml.Document.rdfa`, and :meth:`~turbohtml.Document.dublin_core`
return just one format each. JSON-LD blocks are parsed with the standard library :mod:`json`; a block that is not valid
JSON, or whose payload is a scalar or ``null`` rather than a node object or array, is skipped, so every entry is a
``dict`` or ``list``. The :attr:`~turbohtml.StructuredData.microformats` field is reserved for a later phase and is an
empty list for now.

RDFa and Dublin Core come off the same walk. RDFa yields :class:`~turbohtml.RdfaItem` records that mirror Microdata:
``property`` keys and the ``typeof`` IRIs expand against the in-scope ``@vocab`` and ``@prefix`` (the RDFa 1.1 initial
context seeds the well-known prefixes), and Dublin Core gathers the ``dc.*``/``dcterms.*`` ``<meta>`` names:

.. testcode::

    doc = turbohtml.parse(
        '<head><meta name="dcterms.creator" content="Ada"></head>'
        '<body><div vocab="http://schema.org/" typeof="Person">'
        '<span property="name">Grace</span></div></body>'
    )
    person = doc.rdfa()[0]
    print(person.type, person.properties)
    print(doc.dublin_core())

.. testoutput::

    ['http://schema.org/Person'] {'http://schema.org/name': ['Grace']}
    {'dcterms.creator': 'Ada'}

***************************
 Find the publication date
***************************

Scrapers also want the article's publication date, the job of ``htmldate``. :func:`turbohtml.extract.dates` scores the
same date signals -- publication/modification ``<meta>`` tags, JSON-LD, ``<time>`` elements, and the URL -- off the
parsed page, and returns which one it read:

.. testcode::

    from turbohtml.extract import DateExtraction, dates

    page = (
        '<meta property="article:published_time" content="2016-12-23">'
        '<meta property="article:modified_time" content="2017-02-01">'
    )
    published = dates(page, DateExtraction(original=True))
    print(f"{published.date} (from the {published.signal})")
    print(dates(page).date)

.. testoutput::

    2016-12-23 (from the meta)
    2017-02-01

The default prefers the modification date; ``DateExtraction(original=True)`` prefers the first-published one. A
:class:`~turbohtml.extract.PublicationDate` carries the formatted ``date`` and the ``signal`` it came from, or the call
returns ``None`` when no date inside the ``[min_date, max_date]`` window is found. The :doc:`htmldate migration guide
</migration/htmldate>` maps the rest of the knobs.

**************************
 Parse a syndication feed
**************************

Scrapers also want the items out of an RSS or Atom feed, the job of ``feedparser``. :func:`turbohtml.extract.feed`
detects the format (RSS 2.0, Atom 1.0, or RDF/RSS-1.0) from the root element and normalizes every item into one shape,
so the same code reads an Atom ``<entry>`` and an RSS ``<item>``:

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
spellings. Timestamps are returned verbatim. :func:`~turbohtml.extract.feed` returns ``None`` for a document with no
``<rss>``, ``<feed>``, or ``<rdf:RDF>`` root. The :doc:`feedparser migration guide </migration/feedparser>` maps the
field names.
