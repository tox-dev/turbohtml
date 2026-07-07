############
 Validation
############

.. currentmodule:: turbohtml.validate

:class:`XMLSchema` and :class:`RelaxNG` validate a document parsed with :func:`turbohtml.parse_xml` against an XSD 1.0
or RELAX NG schema. A schema compiles once in the C core; each :meth:`~XMLSchema.validate` call walks the instance tree
there and returns a :class:`ValidationResult` -- a ``valid`` flag plus a tuple of :class:`ValidationError` records, one
per violation, each locating the offending node. The Python layer only shapes the input and wraps the C result.

.. autoclass:: XMLSchema
    :members:
    :inherited-members:

.. autoclass:: RelaxNG
    :members:
    :inherited-members:

.. autoclass:: ValidationResult
    :members:

.. autoclass:: ValidationError
    :members:

.. autoexception:: SchemaValidationError
    :members:
