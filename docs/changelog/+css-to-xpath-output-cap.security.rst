Nested ``:nth-child(... of S)`` selectors no longer translate ``S`` once per use, and a selector whose XPath would pass
16,777,216 characters raises :class:`~turbohtml.convert.ExpressionError` from :func:`~turbohtml.convert.css_to_xpath`
instead of building an unbounded expression.
