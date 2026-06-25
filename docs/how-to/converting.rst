###################################
 Translate a CSS selector to XPath
###################################

******************************************
 Get the XPath for a selector (cssselect)
******************************************

:func:`turbohtml.convert.css_to_xpath` turns a CSS selector into the XPath 1.0 expression that selects the same
elements, the job `cssselect <https://github.com/scrapy/cssselect>`_ does for ``lxml``/``parsel``/``pyquery``. Because
turbohtml runs both engines in-process, the result also runs straight through :meth:`turbohtml.Node.xpath`:

.. testcode::

    from turbohtml.convert import css_to_xpath

    print(css_to_xpath("div.card a[href]"))

.. testoutput::

    descendant-or-self::div[@class and contains(concat(' ', normalize-space(@class), ' '), ' card ')]/descendant-or-self::*/a[@href]

The ``descendant-or-self::`` prefix searches the whole subtree, the same default ``cssselect`` uses, so the expression
selects from anywhere under the context node. The two engines agree on the result:

.. testcode::

    import turbohtml

    doc = turbohtml.parse('<div class="card"><a href="/a">a</a></div><a href="/b">b</a>')
    by_css = doc.select("div.card a[href]")
    by_xpath = [n for n in doc.root.xpath(css_to_xpath("div.card a[href]")) if isinstance(n, turbohtml.Element)]
    print([a.attr("href") for a in by_css], [a.attr("href") for a in by_xpath])

.. testoutput::

    ['/a'] ['/a']

*************************
 Port code off cssselect
*************************

Code that called ``cssselect.HTMLTranslator().css_to_xpath(css)`` swaps the import for
:class:`turbohtml.convert.Translator` (re-exported as ``HTMLTranslator``) and keeps the call. Pass a tighter ``prefix``
-- ``child::`` or ``""`` -- to scope the match instead of searching the whole subtree:

.. testcode::

    from turbohtml.convert import Translator

    translator = Translator()
    print(translator.css_to_xpath("p:nth-child(2n+1)"))
    print(translator.css_to_xpath("li", prefix="child::"))

.. testoutput::

    descendant-or-self::p[(count(preceding-sibling::*)) >= 0 and (count(preceding-sibling::*)) mod 2 = 0]
    child::li

***********************************
 Handle an untranslatable selector
***********************************

XPath 1.0 cannot express a relational ``:has()``, the input-state pseudo-classes, ``:lang()``, or an ``*-of-type`` with
no concrete element type, so those raise :class:`turbohtml.convert.SelectorSyntaxError`. Run the selector directly with
:meth:`turbohtml.Node.select` instead, which has the full CSS engine:

.. testcode::

    from turbohtml.convert import SelectorSyntaxError, css_to_xpath

    try:
        css_to_xpath("div:has(a)")
    except SelectorSyntaxError as error:
        print("fall back to select():", error)

.. testoutput::

    fall back to select(): the pseudo-class has no XPath 1.0 translation
