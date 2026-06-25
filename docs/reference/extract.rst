#########
 Extract
#########

.. module:: turbohtml.extract

Pull content and data out of HTML. Extraction runs through the node methods, and the records those methods return are
re-exported from this namespace for discoverability -- :class:`~turbohtml.Article`, :class:`~turbohtml.Link`,
:class:`~turbohtml.StructuredData`, and :class:`~turbohtml.MicrodataItem` -- while staying importable from the package
root. The content, date, and URL helpers that round out this namespace land with their feature work.

************************************
 Paragraph-level boilerplate labels
************************************

:meth:`~turbohtml.Node.main_content` and :meth:`~turbohtml.Node.article` answer *which subtree is the article* and hand
back a single element; ``justext`` and ``boilerpy3`` answer the finer *for every block of text, content or boilerplate?*
and expose a per-paragraph flag. :func:`boilerplate` adds that view as a thin layer over the same C content scoring: it
locates the article region with :meth:`~turbohtml.Node.main_content`, then labels every leaf text block by whether it
falls inside the region and clears the :class:`Extraction` thresholds. See :doc:`/explanation/main-content` for the
scoring this builds on.

.. autofunction:: boilerplate

.. autoclass:: Extraction
    :members: justext, goose3

.. autoclass:: Paragraph
    :members:
