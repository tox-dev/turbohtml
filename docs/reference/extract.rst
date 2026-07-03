#########
 Extract
#########

.. module:: turbohtml.extract

Pull content and data out of HTML. Extraction runs through the node methods, and the records those methods return are
re-exported from this namespace for discoverability -- :class:`~turbohtml.Article`, :class:`~turbohtml.Link`,
:class:`~turbohtml.StructuredData`, and :class:`~turbohtml.MicrodataItem` -- while staying importable from the package
root.

:func:`boilerplate` adds the per-paragraph view of :doc:`the main-content scoring </explanation/main-content>`: each
paragraph unit of a page classified good or boilerplate, the ``justext`` / ``boilerpy3`` call shape.

.. autofunction:: boilerplate

.. autoclass:: Paragraph

.. autoclass:: Extraction
    :members: justext

:func:`dates` recovers a page's publication or modification date from its ``<meta>`` tags, JSON-LD, ``<time>`` elements,
and URL, the standalone entry point ``htmldate`` exposes (:doc:`htmldate guide </migration/htmldate>`). It returns a
:class:`PublicationDate` naming the signal the date came from, configured by a frozen :class:`DateExtraction`.

.. autofunction:: dates

.. autoclass:: PublicationDate

.. autoclass:: DateExtraction

The URL helpers are successors to ``courlan`` and the ``w3lib.url`` canonicalization surface: they normalize per the
`WHATWG URL standard <https://url.spec.whatwg.org/>`_ and share one frozen :class:`UrlCleaning` config (:doc:`how-to
</how-to/links>`, :doc:`courlan guide </migration/courlan>`).

.. autofunction:: clean_url

.. autofunction:: normalize_url

.. autofunction:: extract_links

.. autoclass:: UrlCleaning
    :members: w3lib

:func:`microdata` extracts a page's top-level HTML Microdata items, the ``microdata.get_items`` call shape
(:doc:`microdata guide </migration/microdata>`). It returns :class:`~turbohtml.MicrodataItem` records whose
:meth:`~turbohtml.MicrodataItem.get`, :meth:`~turbohtml.MicrodataItem.get_all`, and
:meth:`~turbohtml.MicrodataItem.json` accessors mirror the library's ``Item`` (documented under
:doc:`/reference/structured-data`).

.. autofunction:: microdata

:func:`opengraph` extracts a page's Open Graph card as an :class:`OpenGraph` mapping, the ``opengraph`` library's
``OpenGraph(html=...)`` call shape (:doc:`opengraph guide </migration/opengraph>`).

.. autofunction:: opengraph

.. autoclass:: OpenGraph
    :members: is_valid
