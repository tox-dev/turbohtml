###############################
 Pulling content out of a page
###############################

Scraping usually wants three things from a page: the article body without the navigation and sidebars, any tables as
data, and the machine-readable metadata the page embeds. This tutorial pulls all three from one page in a single parse.

Parse the page once. It carries an article, a table, a ``<title>``, an OpenGraph tag, and a JSON-LD block:

.. testcode::

    import turbohtml

    page = turbohtml.parse(
        '<html lang="en"><head><title>Q3 report</title>'
        '<meta property="og:title" content="Q3 sales">'
        '<script type="application/ld+json">{"@type": "Report", "name": "Q3"}</script></head>'
        "<body><nav>Home</nav>"
        "<article><h1>Q3 sales</h1>"
        "<p>Revenue rose across every region this quarter, led by strong demand in the west.</p>"
        "<table><tr><th>Region</th><th>Revenue</th></tr>"
        "<tr><td>West</td><td>120</td></tr><tr><td>East</td><td>90</td></tr></table>"
        "</article></body></html>"
    )

*********************
 Isolate the article
*********************

:meth:`~turbohtml.Node.main_content` scores the page and returns the dominant content element, dropping the ``<nav>``.
:meth:`~turbohtml.Node.article` goes further and returns an :class:`~turbohtml.Article` record with the metadata beside
the body -- title, byline, date, description, and language:

.. testcode::

    print(page.main_content().tag)
    art = page.article()
    print(art.title, art.lang)

.. testoutput::

    article
    Q3 sales en

That one call replaces trafilatura or newspaper3k. The :doc:`/how-to/main-content` guide covers per-paragraph
classification and the scoring knobs.

****************
 Read the table
****************

:meth:`~turbohtml.Element.records` keys the header row over each later row, so a table becomes a list of dicts -- the
shape a ``pandas.read_html`` user feeds to a ``DataFrame``, with no pandas dependency:

.. testcode::

    table = page.find("table")
    for record in table.records():
        print(record)

.. testoutput::

    {'Region': 'West', 'Revenue': '120'}
    {'Region': 'East', 'Revenue': '90'}

***************************
 Collect the embedded data
***************************

:meth:`~turbohtml.Document.structured_data` walks the page once and returns every embedded format -- JSON-LD, Microdata,
OpenGraph, RDFa -- as a :class:`~turbohtml.StructuredData` record, the ``extruct`` successor:

.. testcode::

    data = page.structured_data()
    print(data.json_ld)
    print(data.opengraph)

.. testoutput::

    [{'@type': 'Report', 'name': 'Q3'}]
    {'og:title': 'Q3 sales'}

Article, table, and metadata, all off the one parse. Next, :doc:`forms` reads and fills form controls.
