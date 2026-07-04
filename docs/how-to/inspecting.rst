#################################
 Inspect a node you already hold
#################################

Work with a node in hand rather than search beneath it: read its :attr:`~turbohtml.Node.text` and
:attr:`~turbohtml.Node.html`, dispatch on its kind with a ``match`` statement, and ask it for a CSS or XPath locator
back to the document root.

***********************************
 Read the text or markup of a node
***********************************

:attr:`~turbohtml.Node.text` is the concatenated character data of a node's subtree, with references decoded;
:attr:`~turbohtml.Node.html` re-serializes the subtree back to HTML (attributes quoted, specials escaped):

.. testcode::

    article = turbohtml.parse("<article><h1>Title</h1><p>Tom &amp; Jerry</p></article>").find("article")
    print(article.text)
    print(article.find("p").html)

.. testoutput::

    TitleTom & Jerry
    <p>Tom &amp; Jerry</p>

``text`` gathers every text node in the subtree, including the contents of ``script`` and ``style`` elements when they
sit inside it; filter those out by walking :attr:`~turbohtml.Node.descendants` yourself when you need only rendered
text.

**************************************
 Match nodes with structural patterns
**************************************

The node types are a sealed hierarchy with :py:data:`~object.__match_args__` set, so a ``match`` statement dispatches on
node kind and unpacks the defining field (``tag`` for an :class:`~turbohtml.Element`, ``data`` for a
:class:`~turbohtml.Text` or :class:`~turbohtml.Comment`):

.. testcode::

    def summarize(node: turbohtml.Node) -> str:
        match node:
            case turbohtml.Element(tag):
                return f"<{tag}>"
            case turbohtml.Text(data):
                return repr(data)
            case turbohtml.Comment(data):
                return f"<!--{data}-->"
            case _:
                return "?"


    print([summarize(child) for child in turbohtml.parse("<p>hi<!--x--><b>bold</b></p>").find("p")])

.. testoutput::

    ["'hi'", '<!--x-->', '<b>']

************************
 Get the path to a node
************************

Once you have an element, ask it for a locator back to the document root, the way browser devtools "copy selector" and
lxml's ``getpath`` do. :meth:`~turbohtml.Element.css_path` returns a CSS selector and
:meth:`~turbohtml.Element.xpath_path` returns a positional XPath; both round-trip, so feeding the result to
:meth:`~turbohtml.Node.select` or :meth:`~turbohtml.Node.xpath` on the document returns exactly that element. Use them
to log a match, store a stable reference, or debug a scrape:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<body><div><p>one</p><p>two</p></div></body>")
    second = doc.select("p")[1]
    print(second.css_path())
    print(second.xpath_path())
    print(doc.select(second.css_path()) == [second])

.. testoutput::

    html > body > div > p:nth-of-type(2)
    /html/body/div/p[2]
    True

The CSS path anchors at the nearest ancestor (or the element itself) carrying a document-unique ``id``, which keeps it
short and stable against reordering; otherwise it descends from the root with ``:nth-of-type()`` steps. The XPath form
is always positional, like ``getpath``:

.. testcode::

    doc = turbohtml.parse('<body><main id="content"><ul><li>a</li><li>b</li></ul></main></body>')
    item = doc.select("li")[1]
    print(item.css_path())
    print(item.xpath_path())

.. testoutput::

    #content > ul > li:nth-of-type(2)
    /html/body/main/ul/li[2]
