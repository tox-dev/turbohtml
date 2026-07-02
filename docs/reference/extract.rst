#########
 Extract
#########

.. module:: turbohtml.extract

Pull content and data out of HTML. Extraction runs through the node methods, and the records those methods return are
re-exported from this namespace for discoverability -- :class:`~turbohtml.Article`, :class:`~turbohtml.Link`,
:class:`~turbohtml.StructuredData`, and :class:`~turbohtml.MicrodataItem` -- while staying importable from the package
root. The date and URL helpers that round out this namespace land with their feature work.

:func:`boilerplate` adds the per-paragraph view of :doc:`the main-content scoring </explanation/main-content>`: each
paragraph unit of a page classified good or boilerplate, the ``justext`` / ``boilerpy3`` call shape.

.. autofunction:: boilerplate

.. autoclass:: Paragraph

.. autoclass:: Extraction
    :members: justext
