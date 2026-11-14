Unwrapping ``script``, ``style``, ``noscript``, ``textarea`` or another raw-text element in a
:func:`~turbohtml.rewrite.rewrite` handler now escapes its content so it stays text, fallback markup inside ``noscript``
included.
