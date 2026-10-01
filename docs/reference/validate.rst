############
 Validation
############

.. currentmodule:: turbohtml.validate

:class:`XMLSchema` and :class:`RelaxNG` validate a document parsed with :func:`turbohtml.parse_xml` against an XSD 1.0
or RELAX NG schema. A schema compiles once in the C core; each :meth:`~XMLSchema.validate` call walks the instance tree
there and returns a :class:`ValidationResult` -- a ``valid`` flag plus a tuple of :class:`ValidationError` records, one
per violation, each locating the offending node. The Python layer only shapes the input and wraps the C result.

Schema compilation and instance validation raise :class:`RecursionError` before processing a tree nested 400 levels or
deeper. This limit keeps the remaining recursive grammar walks within small worker-thread stacks; the validator does not
return partial results.

Schema compilation raises :class:`ValueError` for an ``xs:pattern`` facet or RELAX NG ``pattern`` parameter that nests
groups deeper than 250 levels, needs more than 2,000,000 automaton states, or has a ``{n,m}`` quantifier with ``n``
above ``m``. The message names the limit and the offset where the pattern reaches it; flatten the nested groups, lower
the repeat counts, or split the pattern into several ``pattern`` facets to stay within the limits. Matching a value
takes time linear in its length.

Compiling a RELAX NG schema raises :class:`ValueError` for a grammar the RELAX NG specification forbids: a ``<ref>``
with no ``name`` attribute (section 4.10), a reference cycle whose expansion never passes through an ``element``
(section 4.19), and an ``interleave`` whose branches can match an element with the same name or can both match text
(section 7.4). The message names the offending ``define`` or construct. Validating against a legal but ambiguous
``choice`` or ``interleave`` takes memory bounded by the schema size, not by the number of child elements.

Compiling an XSD schema raises :class:`ValueError` for a reference that does not resolve -- an ``xs:element``,
``xs:group``, ``xs:attribute``, or ``xs:attributeGroup`` ``ref``, an element or attribute ``type``, or an extension or
restriction ``base`` whose QName names no built-in or declared component -- and the message names the unresolved QName.
The same error covers a reference cycle among ``xs:group`` refs, ``xs:attributeGroup`` refs, or complex-type
derivations, and any such chain longer than 100 hops. A schema that declares an ``xs:unique``, ``xs:key``, or
``xs:keyref`` identity constraint raises it too, because the validator does not enforce the constraint and would accept
documents that break it.

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
