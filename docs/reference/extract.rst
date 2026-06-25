#########
 Extract
#########

.. module:: turbohtml.extract

Pull content and data out of HTML. Extraction runs through the node methods, and the records those methods return are
re-exported from this namespace for discoverability -- :class:`~turbohtml.Article`, :class:`~turbohtml.Link`,
:class:`~turbohtml.StructuredData`, and :class:`~turbohtml.MicrodataItem` -- while staying importable from the package
root. The remaining content and URL helpers land with their feature work.

******************
 Publication date
******************

:func:`~turbohtml.dates` is the standalone publication-date entry point, a successor to `htmldate
<https://htmldate.readthedocs.io>`_'s ``find_date``. It reuses the declared-metadata path
:meth:`~turbohtml.Node.article` already walks -- ``<time>`` elements, ``article:published_time`` and common date
``<meta>`` tags -- and layers JSON-LD ``datePublished``/``dateModified``, last-modified metas, and a ``/YYYY/MM/DD/``
date in the canonical or OpenGraph URL on top, validating the result against an optional window. A
:class:`DateExtraction` config carries the knobs (it is also importable as :class:`turbohtml.DateExtraction`); ``None``
returns the most recent date as ``YYYY-MM-DD``.

.. currentmodule:: turbohtml

.. autofunction:: dates

.. autoclass:: DateExtraction
    :members: published, fast
