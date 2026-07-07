######
 XSLT
######

turbohtml ships an XSLT 1.0 processor in :mod:`turbohtml.transform`. This page explains how it is built on the existing
XPath engine and why it stops where it does.

************************
 One evaluator, not two
************************

An XSLT processor is mostly an XPath processor with a template-dispatch loop wrapped around it. turbohtml already has a
compiled XPath 1.0 engine (:doc:`queries`), so the transform reuses it wholesale: every ``select`` expression, every
``test``, every ``use`` and ``value`` is compiled and evaluated by the same engine that backs
:meth:`~turbohtml.Node.xpath`. The XSLT layer adds only what XPath cannot express -- template matching, conflict
resolution, the instruction walk, and the result-tree serializer. This is why the whole engine is a single C file
alongside the XPath sources rather than a parallel path evaluator.

The XSLT-only functions ride the same seam. ``current()``, ``key()``, ``generate-id()``, ``format-number()``, and
``system-property()`` are not XPath core functions, so they are dispatched through the XPath engine's extension hook:
the evaluator, on an unknown function name, calls back into the XSLT layer, which resolves them against the current
node, the key tables, and the decimal-format rules. The engine never learns about XSLT; XSLT never re-implements
function dispatch.

************************
 Patterns by membership
************************

XSLT matches a node against a pattern like ``chapter/para`` or ``para[1]``; XPath selects a node-set from a context. The
two meet through a definition in the spec: a node matches a pattern exactly when it is a member of the node-set the
pattern selects from some ancestor. turbohtml uses that directly. A pattern is rewritten to an equivalent absolute
expression (a relative pattern gains a leading ``//``), evaluated once against the source root, and the selected nodes
are recorded in a hash set. Testing whether a node matches is then an O(1) set membership test, and each pattern is
evaluated over the document only once regardless of how many nodes are dispatched against it. Because the source tree is
immutable for the duration of a transform, and XSLT 1.0 forbids variables and ``current()`` inside a pattern, the match
set is stable once built.

Conflict resolution (`section 5.5 <https://www.w3.org/TR/xslt-10/#conflict>`_) then reduces to ordering: rules are
sorted once by descending import precedence, then priority, then document position, so the first matching rule in that
order is the winner. Default priorities follow the spec -- ``0`` for a lone name test, ``-0.5`` for an unqualified node
test, ``0.5`` for anything with a predicate or more than one step -- computed from the pattern at compile time.

****************
 Where it stops
****************

The engine models a single stylesheet document. ``xsl:include`` and ``xsl:import`` would load other files, and
``document()`` would load arbitrary URLs; turbohtml resolves none of them, both to stay free of a fetch/filesystem
surface and because a self-contained transform is the common case. Import precedence therefore has one level, and the
conflict resolution that matters in practice -- priority and document order -- is exact. A result tree fragment converts
to its string value when used through XPath (the strict XSLT 1.0 rule), and ``xsl:copy-of`` of a fragment variable
copies its nodes. Output shaping beyond the method choice -- ``indent``, ``doctype-*``, ``cdata-section-elements`` -- is
not applied; the serializer is turbohtml's own, so its byte layout follows :doc:`serialization`, not libxslt's.

*******************
 Conformance basis
*******************

The processor is validated against libxslt's own XSLT 1.0 Recommendation corpus -- the ``REC`` and ``REC2``
stylesheet/source/expected-output triples it ships, which are the worked examples from the spec run through the
reference implementation. ``tests/conformance/test_xslt_conformance.py`` runs turbohtml over every triple and asserts
byte-equal output (whitespace normalized per output method). Of the 79 cases, 56 pass exactly; the remaining 23 use
features listed under "Where it stops" (whitespace stripping, namespace-alias, attribute sets, multi-level
``xsl:number``, ``cdata-section-elements``, extension elements, and the html method's meta injection) and are marked
``xfail`` with the spec section each needs. The corpus is the pinned ``tests/conformance/libxslt`` submodule.

For the XPath engine underneath, see :doc:`queries`; for a port from lxml, see :doc:`/migration/lxml`.
