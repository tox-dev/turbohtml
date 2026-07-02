#########
 Extract
#########

.. module:: turbohtml.extract

Pull content and data out of HTML. Extraction runs through the node methods, and the records those methods return are
re-exported from this namespace for discoverability -- :class:`~turbohtml.Article`, :class:`~turbohtml.Link`,
:class:`~turbohtml.StructuredData`, and :class:`~turbohtml.MicrodataItem` -- while staying importable from the package
root. The date helpers that round out this namespace land with their feature work.

:func:`boilerplate` adds the per-paragraph view of :doc:`the main-content scoring </explanation/main-content>`: each
paragraph unit of a page classified good or boilerplate, the ``justext`` / ``boilerpy3`` call shape.

.. autofunction:: boilerplate

.. autoclass:: Paragraph

.. autoclass:: Extraction
    :members: justext

The URL helpers are successors to ``courlan`` and the ``w3lib.url`` canonicalization surface: they normalize per the
`WHATWG URL standard <https://url.spec.whatwg.org/>`_ and share one frozen :class:`UrlCleaning` config (:doc:`how-to
</how-to/links>`, :doc:`courlan guide </migration/courlan>`).

.. autofunction:: clean_url

.. autofunction:: normalize_url

.. autofunction:: extract_links

.. autoclass:: UrlCleaning
    :members: w3lib
