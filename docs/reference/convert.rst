#########
 Convert
#########

.. module:: turbohtml.convert

Translate between the query languages turbohtml speaks. :func:`css_to_xpath` turns a CSS selector list into an
equivalent XPath 1.0 expression, the job `cssselect <https://github.com/scrapy/cssselect>`_ does for lxml, parsel, and
pyquery. The translation is a C pass over the parsed selector, and the emitted expression selects the same nodes as the
selector does under :meth:`turbohtml.Node.select` -- including the WHATWG case-insensitive attribute set, Selectors 4
``:empty``, and the exact ``fieldset``/``legend`` rule for ``:disabled``, where cssselect approximates. Every predicate
is context-free (no bare ``position()`` tests), so the expression stays valid inside larger XPath expressions.

.. autofunction:: css_to_xpath

:func:`css_specificity` weighs the same parsed selector instead of translating it, returning the ``(a, b, c)`` triple
`CSS Selectors Level 4 §17 <https://www.w3.org/TR/selectors-4/#specificity-rules>`_ defines, one per comma-separated
selector, the value cssselect exposes as ``Selector.specificity()``.

.. autofunction:: css_specificity

.. autoclass:: GenericTranslator
    :members: css_to_xpath

.. autoclass:: HTMLTranslator

A selector the CSS grammar rejects raises :class:`turbohtml.SelectorSyntaxError` (the one error every selector-parse
path shares); a valid selector XPath 1.0 cannot express raises :exc:`ExpressionError`, under the cssselect-shaped
:exc:`SelectorError` base.

.. autoexception:: SelectorError

.. autoexception:: ExpressionError
