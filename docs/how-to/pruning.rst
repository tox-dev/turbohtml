###############################
 Trim a document to a selector
###############################

Keep only the part of a parsed document you care about with :meth:`~turbohtml.Node.prune`, the parse-then-keep pattern
BeautifulSoup spelled with a ``SoupStrainer``.

:meth:`~turbohtml.Node.prune` keeps only the descendants matching a CSS selector, plus their ancestors up to the node it
is called on and the whole subtree under each match, and removes everything else in place. This is the parse-then-keep
pattern BeautifulSoup spelled with a ``SoupStrainer``: parse the whole document the WHATWG way, then shrink it to the
part you care about. It returns the node, so it chains off :func:`~turbohtml.parse`:

.. testcode::

    markup = "<body><nav>skip</nav><main><article>keep<b>me</b></article><aside>drop</aside></main></body>"
    doc = turbohtml.parse(markup).prune("article")
    print(doc.select_one("body").serialize())

.. testoutput::

    <body><main><article>keep<b>me</b></article></main></body>

The ancestors of a match stay so the match keeps its place in the tree, and the match's own subtree stays whole, so the
``<b>`` and the text survive while the unrelated ``<nav>`` and ``<aside>`` go. A selector that matches nothing empties
the subtree.
