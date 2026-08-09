#################
 Structured data
#################

.. currentmodule:: turbohtml

:meth:`Document.structured_data` pulls every machine-readable metadata format a page embeds in one walk, with the
per-format helpers :meth:`Document.json_ld`, :meth:`Document.opengraph`, :meth:`Document.microdata`,
:meth:`Document.rdfa`, and :meth:`Document.dublin_core` beside it. The walk runs in the C core; the typed, read-only
result records below are handed back from it.

The nested-record methods :meth:`Document.microdata`, :meth:`Document.rdfa`, and :meth:`Document.structured_data` raise
:exc:`RecursionError` for documents nested 400 levels or deeper, before they build result records.

.. autoclass:: StructuredData
    :members:

.. autoclass:: MicrodataItem
    :members:

.. autoclass:: RdfaItem
    :members:
