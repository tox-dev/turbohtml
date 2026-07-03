################
 From cssselect
################

.. package-meta:: cssselect scrapy/cssselect

`cssselect <https://cssselect.readthedocs.io/>`_ translates a CSS selector to an XPath 1.0 expression; it is the engine
behind ``lxml.cssselect``, parsel, and pyquery. :func:`turbohtml.convert.css_to_xpath` does the same job as a single C
pass over the parsed selector, and :class:`turbohtml.convert.GenericTranslator` /
:class:`turbohtml.convert.HTMLTranslator` keep cssselect's translator-object shape so a port is mechanical.

***************
 Why turbohtml
***************

turbohtml runs both a CSS selector engine and an XPath engine in-process, so its test suite validates the translation
differentially: for cssselect's own test corpus, the emitted XPath selects exactly the nodes turbohtml's native CSS
engine matches, and libxml2 evaluates the emitted expression to the same node-set as cssselect's output. The C
translator is 4x faster than cssselect on a bare type selector and 36x to 45x faster on realistic selectors, where
cssselect's Python tokenizer dominates. Each ratio is against turbohtml:

.. bench-table::
    :file: bench/cssselect.json

The translation also covers ground cssselect leaves out, and follows the current specs where cssselect approximates:

- ``:is()`` / ``:where()`` / ``:not()`` accept full complex selectors (``:is(nav a)``), and ``:has()`` accepts the
  relative forms ``:has(> a)``, ``:has(+ a)``, ``:has(~ a)``.
- ``:nth-child(An+B of S)``, ``:disabled``/``:enabled``/``:checked``/``:required``/``:optional``/
  ``:read-only``/``:read-write`` translate per WHATWG HTML, including the ``fieldset`` first-``legend`` exemption
  cssselect marks as a ``FIXME``.
- ``:empty`` follows Selectors 4 (whitespace-only elements match); the WHATWG case-insensitive attribute set (``type``,
  ``lang``, ``rel``, ...) compares values case-insensitively, as browsers do.
- Every emitted predicate is context-free (no bare ``position()`` tests), so you can embed a fragment in a larger
  expression without changing its meaning.

*********************
 Translating the API
*********************

cssselect exposes ``css_to_xpath`` as a translator method; turbohtml keeps that shape and adds a plain function:

.. code-block:: python

    # cssselect
    from cssselect import HTMLTranslator

    xpath = HTMLTranslator().css_to_xpath("ul > li.item")

    # turbohtml, either shape
    from turbohtml.convert import HTMLTranslator, css_to_xpath

    xpath = HTMLTranslator().css_to_xpath("ul > li.item")
    xpath = css_to_xpath("ul > li.item")

.. testcode::

    from turbohtml.convert import css_to_xpath

    print(css_to_xpath("div#main a[href^='https']"))

.. testoutput::

    descendant-or-self::div[@id = 'main']/descendant::a[starts-with(@href, 'https')]

The ``prefix`` argument works as in cssselect (default ``descendant-or-self::``), and the error types keep their names:
``SelectorSyntaxError`` for a selector the grammar rejects, ``ExpressionError`` for a valid selector XPath 1.0 cannot
express, both under a common ``SelectorError``.

.. testcode::

    from turbohtml.convert import ExpressionError, SelectorSyntaxError, css_to_xpath

    try:
        css_to_xpath("li:")
    except SelectorSyntaxError as error:
        print(error)
    try:
        css_to_xpath(":dir(rtl)")
    except ExpressionError as error:
        print(error)

.. testoutput::

    invalid CSS selector "li:": expected an identifier at position 3
    :dir() cannot be expressed in XPath 1.0

Pitfalls
========

- The emitted string differs from cssselect's; only the selected node-set is the contract. Compare results rather than
  expression text.
- Both translator classes apply the HTML rules (element and attribute names lowercase). turbohtml has no XML
  (case-sensitive) mode, so ``HTMLTranslator(xhtml=True)`` only records the flag.
- ``:scope`` translates only as the leftmost compound (``:scope > div``), matching how cssselect's own translation
  behaves; anywhere else raises ``ExpressionError``.
- cssselect's non-standard extensions ``:contains()`` and ``[attr!=value]`` are not CSS and do not parse; they raise
  ``SelectorSyntaxError``. Use the XPath ``contains(., ...)`` predicate or ``:not([attr=value])`` directly.
- ``[lang|="en"]``, ``[type=CHECKBOX]``, ``li:empty`` on whitespace-only elements, and ``:disabled`` on hidden inputs or
  around ``legend`` select per the current specs, so their node-sets can differ from cssselect's approximations (the
  differential suite pins each divergence).
