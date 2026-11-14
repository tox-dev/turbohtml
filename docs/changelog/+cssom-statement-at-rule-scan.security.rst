A stylesheet with many statement at-rules, such as ``@import``, no longer takes quadratic time to parse in
:class:`~turbohtml.cssom.StyleSheet` or :func:`~turbohtml.cssom.computed_style`.
