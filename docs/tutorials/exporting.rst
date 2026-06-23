################
 Export to text
################

Once you have the node you want, :meth:`~turbohtml.Node.to_markdown` turns it into GitHub-Flavored Markdown in one call,
so a scraping script ends with Markdown instead of a tag soup:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<article><h2>Tea</h2><p>Steep <em>green</em> tea for <b>3</b> minutes.</p></article>")
    print(doc.find("article").to_markdown())

.. testoutput::

    ## Tea

    Steep *green* tea for **3** minutes.

**********************
 Pull out the article
**********************

A real page wraps that prose in navigation, sidebars and footers. When you only want the article and do not know its
selector, :meth:`~turbohtml.Node.main_content` finds the dominant content element for you by scoring the tree, and
:meth:`~turbohtml.Node.main_text` hands you its text directly:

.. testcode::

    page = turbohtml.parse(
        "<nav><a href='/'>Home</a></nav>"
        "<main class='post'><h2>Tea</h2>"
        "<p>Steeping green tea for three minutes draws out its flavor without turning it bitter.</p></main>"
        "<footer>(c) 2026</footer>"
    )
    print(page.main_content().tag)
    print(page.main_text())

.. testoutput::

    main
    Tea

    Steeping green tea for three minutes draws out its flavor without turning it bitter.

********************
 Sanitize a snippet
********************

Export runs the other way too: when the HTML comes from someone else, clean it with :func:`turbohtml.sanitizer.sanitize`
before you embed it. The default policy keeps a safe subset of tags, drops event handlers and dangerous URL schemes, and
escapes the elements it removes rather than discarding their text:

.. testcode::

    from turbohtml.sanitizer import sanitize

    print(sanitize('<a href="javascript:alert(1)">x</a> <b onclick="y()">bold</b><script>bad()</script>'))

.. testoutput::

    <a>x</a> <b>bold</b>&lt;script&gt;bad()&lt;/script&gt;

That is the whole tree API. Head to the :doc:`/how-to/index` guides for task-focused recipes, the
:doc:`/migration/index` guide if you are coming from another HTML library, or the :doc:`/reference` for the exact
signatures.
