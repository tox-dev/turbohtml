#################
 Structured data
#################

.. currentmodule:: turbohtml

:meth:`Document.structured_data` pulls every machine-readable metadata format a page embeds from one document snapshot.
The C core also exposes per-format helpers through :meth:`Document.json_ld`, :meth:`Document.opengraph`,
:meth:`Document.microdata`, :meth:`Document.rdfa`, and :meth:`Document.dublin_core`. Each method returns the typed,
read-only records below.

The nested-record methods :meth:`Document.microdata`, :meth:`Document.rdfa`, and :meth:`Document.structured_data` raise
:exc:`RecursionError` for more than 400 nested Microdata or RDFa records. Microdata also rejects nested-item cycles made
with ``itemref``. Plain DOM nesting does not count toward the record limit.

.. autoclass:: StructuredData
    :members:

.. autoclass:: MicrodataItem
    :members:

.. autoclass:: RdfaItem
    :members:
