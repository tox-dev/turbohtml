Minifying the output of :func:`~turbohtml.clean.minify_css` or :func:`~turbohtml.clean.minify_css_inline` again now
returns the same text when an identifier or at-keyword ends in a hex escape, such as ``.\31`` or ``\e``. The space that
closes the escape is kept wherever a later pass would write it back.
