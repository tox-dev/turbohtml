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
with ``itemref``, more than 10,000 repeats of the items that several others share through ``itemref``, and more than
100,000 repeats of the plain property values they share. Plain DOM nesting does not count toward the record limit. A
Microdata or RDFa property that takes its text content as its value is left out, without an error, when more than 32
such properties nest in one another from it down. :meth:`Document.json_ld` applies the same 400-level ceiling to JSON-LD
array and object nesting; it skips a block past the ceiling and does not raise.

.. autoclass:: StructuredData
    :members:

.. autoclass:: MicrodataItem
    :members:

.. autoclass:: RdfaItem
    :members:
