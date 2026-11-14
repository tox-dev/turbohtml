Limit the bracket nesting :func:`~turbohtml.clean.linkify` and :class:`~turbohtml.clean.LinkDetector` follow in a URL to
32 levels so a run of links that each precede an unclosed bracket no longer takes quadratic time to scan.
