#########
 Extract
#########

.. module:: turbohtml.extract

Pull content and data out of HTML. Extraction runs through the node methods, and the records those methods return are
re-exported from this namespace for discoverability -- :class:`~turbohtml.Article`, :class:`~turbohtml.Link`,
:class:`~turbohtml.StructuredData`, and :class:`~turbohtml.MicrodataItem` -- while staying importable from the package
root. The content and date helpers that round out this namespace land with their feature work.

******
 URLs
******

Clean, canonicalize, and harvest the URLs in a document, the successor to ``courlan`` and ``w3lib.url``. These layer a
small normalization pass over the link engine (:meth:`~turbohtml.Node.links` / :meth:`~turbohtml.Node.resolve_links`):
:func:`clean_url` scrubs tracking junk and stray markup off a raw href, :func:`normalize_url` canonicalizes a single
absolute URL, and :func:`extract_links` returns the cleaned, deduplicated web links of a page. A shared
:class:`UrlCleaning` configuration controls how aggressively each one rewrites. Unlike ``courlan``, these helpers do not
guess a page's language or rank a link's crawl-worthiness; they normalize and deduplicate only.

.. autofunction:: clean_url

.. autofunction:: normalize_url

.. autofunction:: extract_links

.. autoclass:: UrlCleaning
    :members: aggressive
