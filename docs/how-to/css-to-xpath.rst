###################################
 Translate a CSS selector to XPath
###################################

Emit an XPath 1.0 expression that selects the same nodes as a CSS selector with :func:`turbohtml.convert.css_to_xpath`,
for systems that speak only XPath.

:func:`turbohtml.convert.css_to_xpath` replaces ``cssselect.HTMLTranslator().css_to_xpath``: it emits an XPath 1.0
expression that selects the same nodes as the CSS selector, for the systems that only speak XPath:

.. testcode::

    from turbohtml.convert import css_to_xpath

    print(css_to_xpath("ul > li.item"))

.. testoutput::

    descendant-or-self::ul/li[@class and contains(concat(' ', normalize-space(@class), ' '), ' item ')]

The default prefix scopes the expression to the context node's subtree, the cssselect convention. Pass a different
``prefix`` to change the anchoring; ``descendant::`` mirrors what :meth:`~turbohtml.Node.select` walks (descendants
only), and ``//`` produces a document-absolute path:

.. testcode::

    print(css_to_xpath("li:first-child", prefix="//"))

.. testoutput::

    //li[not(preceding-sibling::*)]

A selector list becomes a union with the prefix on each arm, and a selector that the CSS grammar rejects raises
:class:`~turbohtml.SelectorSyntaxError` while a valid one that XPath 1.0 cannot express (``:dir()``, an of-type
pseudo-class without a type) raises :class:`~turbohtml.convert.ExpressionError`:

.. testcode::

    from turbohtml.convert import ExpressionError

    print(css_to_xpath("h1, h2", prefix="//"))
    try:
        css_to_xpath("*:first-of-type")
    except ExpressionError as error:
        print(error)

.. testoutput::

    //h1 | //h2
    the of-type pseudo-classes need a type selector
