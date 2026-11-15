############
 Validation
############

.. currentmodule:: turbohtml.validate

:class:`XMLSchema` and :class:`RelaxNG` validate a document parsed with :func:`turbohtml.parse_xml` against an XSD 1.0
or RELAX NG schema. A schema compiles once in the C core; each :meth:`~XMLSchema.validate` call walks the instance tree
there and returns a :class:`ValidationResult` -- a ``valid`` flag plus a tuple of :class:`ValidationError` records, one
per violation, each locating the offending node. The Python layer only shapes the input and wraps the C result.

Passing a node that is neither a document nor an element, such as a text node, raises :class:`TypeError` from
:meth:`~XMLSchema.validate`, :meth:`~XMLSchema.is_valid` and :meth:`~XMLSchema.assert_valid`.

Schema compilation and instance validation raise :class:`RecursionError` before processing a tree nested 400 levels or
deeper. This limit keeps the remaining recursive grammar walks within small worker-thread stacks; the validator does not
return partial results. In a RELAX NG grammar the limit counts the levels of a ``define`` at each ``ref`` to it, up to
the next ``element``: compilation raises :class:`RecursionError` when references nest patterns 400 levels or deeper with
no ``element`` in between.

:meth:`~XMLSchema.validate` and :meth:`~XMLSchema.assert_valid` collect the first 1,000 errors of a document; further
violations still make it invalid but are not listed. Each segment of :attr:`ValidationError.path` keeps the first 64
characters of an element name and ends a longer one with a horizontal ellipsis (U+2026).

Schema compilation raises :class:`ValueError` for an ``xs:pattern`` facet or RELAX NG ``pattern`` parameter that nests
groups deeper than 250 levels, needs more than 2,000,000 automaton states, or has a ``{n,m}`` quantifier with ``n``
above ``m``. The message names the limit and the offset where the pattern reaches it; flatten the nested groups, lower
the repeat counts, or split the pattern into several ``pattern`` facets to stay within the limits. Matching a value
takes time linear in its length.

The range facets ``minInclusive``, ``maxInclusive``, ``minExclusive`` and ``maxExclusive`` compare a number by value and
an ``xs:date``, ``xs:dateTime``, ``xs:time`` or ``xs:duration`` by the order XSD 1.0 part 2 gives the type. Years are
numbered as in XSD 1.1, where 0000 is 1 BCE and -0001 is 2 BCE. That order is partial: a value with a timezone and one
without are ordered only when they lie more than fourteen hours apart, and two durations only when they compare the same
way from each of the specification's four reference dates, so ``P1M`` is neither below nor above ``P30D``. A value the
order does not place against a bound fails the facet, and so does every value when the bound is not itself a value of
the type. The range facets are not enforced on ``xs:gYear``, ``xs:gYearMonth``, ``xs:gMonth``, ``xs:gMonthDay`` or
``xs:gDay``: those validate as strings.

Compiling a RELAX NG schema raises :class:`ValueError` for a grammar the RELAX NG specification forbids: a ``<ref>``
with no ``name`` attribute (section 4.10), a reference cycle whose expansion never passes through an ``element``
(section 4.19), and an ``interleave`` whose branches can match an element with the same name or can both match text
(section 7.4). The message names the offending ``define`` or construct. The check compares name classes, so an
``anyName`` or ``nsName`` that overlaps the name class of another branch is a conflict. It looks through each ``<ref>``,
counts a ``mixed`` as text, and, when any part of a name says ``combine="interleave"``, checks every part against the
parts before it, as section 4.17 joins them all by ``interleave``. Once the interleave check has done about 33 million
steps of work, compilation raises :class:`ValueError` with ``RELAX NG grammar too large to check interleave branches``.
Validating against a legal but ambiguous ``choice`` or ``interleave`` takes memory bounded by the schema size, not by
the number of child elements.

A RELAX NG ``include`` or ``externalRef`` reads its ``href`` from disk when the schema compiles, resolved against
``base_url`` and any ``xml:base`` (sections 4.5 to 4.7). Compilation raises :class:`ValueError` when the schema
references a file without a ``base_url``, when an ``href`` carries a fragment, names a remote URL or a UNC path, or
resolves outside ``include_root``, when references form a loop, when an ``externalRef`` loads something other than a
pattern or an ``include`` something other than a ``grammar``, and when an ``include`` overrides a ``start`` or
``define`` the included grammar does not have. A file that cannot be read raises :class:`OSError`.

Compiling an XSD schema raises :class:`ValueError` for a reference that does not resolve -- an ``xs:element``,
``xs:group``, ``xs:attribute``, or ``xs:attributeGroup`` ``ref``, an element or attribute ``type``, or an extension or
restriction ``base`` whose QName names no built-in or declared component -- and the message names the unresolved QName.
The same error covers a reference cycle among ``xs:group`` refs, ``xs:attributeGroup`` refs, or complex-type
derivations, and any such chain longer than 100 hops. It also covers a complex type whose ``xs:group``,
``xs:attributeGroup``, and base-type references expand to more than 20,000 particles and attribute declarations. A
schema that declares an ``xs:unique``, ``xs:key``, or ``xs:keyref`` identity constraint raises it too, because the
validator does not enforce the constraint and would accept documents that break it. An ``xs:group`` particle without a
``ref`` compiles and matches no content.

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
