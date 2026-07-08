#######################
 Finding what you need
#######################

You have a parsed page and want a few nodes out of it: the titles in a list, a price next to each one, the link for a
given row. This tutorial parses one small listing and reaches those nodes three ways -- a CSS selector, the ``find``
filter, and an XPath expression -- so you know which to reach for.

Start with a page. It is a book listing, two rows, each with a title link and a price:

.. testcode::

    import turbohtml

    page = turbohtml.parse(
        "<main><ul class='books'>"
        "<li class='book'><a class='title' href='/b/1'>Dune</a><span class='price'>9.99</span></li>"
        "<li class='book'><a class='title' href='/b/2'>Hyperion</a><span class='price'>7.50</span></li>"
        "</ul></main>"
    )

*****************
 Select with CSS
*****************

:meth:`~turbohtml.Node.select` returns every descendant matching a CSS selector, in document order.
:meth:`~turbohtml.Node.select_one` returns the first match or ``None``. The selector reads like the structure: a
``.book`` list item, then its ``a.title`` link:

.. testcode::

    print([a.text for a in page.select("li.book a.title")])
    print(page.select_one("li.book .price").text)

.. testoutput::

    ['Dune', 'Hyperion']
    9.99

Reach for CSS first. It is the shortest way to say "these descendants," and the matcher covers the full Selectors Level
4 grammar the :doc:`/how-to/selecting` guide lays out.

******************
 Narrow with find
******************

When the thing you want is a value rather than a selector shape -- the row whose ``href`` is exactly ``/b/2`` --
:meth:`~turbohtml.Node.find` filters on the tag, an attribute, the class list, or the text, and returns the first match:

.. testcode::

    print(page.find("a", attrs={"href": "/b/2"}).text)

.. testoutput::

    Hyperion

``find`` takes strings, regexes, callables, and lists, so it expresses matches a selector cannot. The
:doc:`/how-to/finding` guide covers the filter grammar.

******************
 Reach with XPath
******************

The same nodes are reachable with XPath 1.0 through :meth:`~turbohtml.Node.xpath`, for code coming from lxml or a system
that already speaks XPath. :meth:`~turbohtml.Node.xpath_one` returns the first result, and an XPath can select text
directly, not just elements:

.. testcode::

    print([node.text for node in page.xpath("//li[@class='book']/a")])
    print(page.xpath_one("//a[@href='/b/1']/text()"))

.. testoutput::

    ['Dune', 'Hyperion']
    Dune

Three routes to the same nodes: CSS for structure, ``find`` for values, XPath when you already think in it. Next,
:doc:`reading-bytes` handles the case where the input is raw bytes of unknown encoding rather than a ready string.
