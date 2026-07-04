################################
 Extract strings and attributes
################################

Pull plain strings out of a page the way Scrapy's parsel does, with :meth:`~turbohtml.Element.attr`,
:meth:`~turbohtml.Node.re`, and :meth:`~turbohtml.Node.re_first`, so scraping code ends with the values it wants rather
than nodes.

Scraping code wants *strings*, not nodes. :meth:`~turbohtml.Element.attr` reads one attribute as a single string (or a
default when it is missing), and :meth:`~turbohtml.Node.re` / :meth:`~turbohtml.Node.re_first` run a regular expression
over a node's text, the same extraction primitives Scrapy's ``parsel`` offers. ``re`` returns the one capturing group
when the pattern has exactly one, otherwise the whole match, so a single pattern pulls the part you want:

.. testcode::

    doc = turbohtml.parse('<p>Order 1138 shipped</p><a href="/p/42">item 42</a>')
    print(doc.select_one("p").re_first(r"Order (\d+)"))
    print(doc.select_one("a").attr("href"))
    print(doc.select_one("a").re(r"/p/(\d+)", attr="href"))

.. testoutput::

    1138
    /p/42
    ['42']

Pass ``attr=`` to run the pattern over an attribute value instead of the text; an absent attribute yields ``[]`` from
``re`` and the default from ``re_first``. Map the call across a :meth:`~turbohtml.Node.select` result to extract from
every match at once:

.. testcode::

    listing = turbohtml.parse('<a href="/p/1">a</a><a href="/p/2">b</a>')
    print([a.re_first(r"\d+", attr="href") for a in listing.select("a")])

.. testoutput::

    ['1', '2']
