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

***********************
 Stripping and imports
***********************

Two features reach outside the single-pass instruction walk. ``xsl:strip-space``/``xsl:preserve-space`` (`section 3.4
<https://www.w3.org/TR/xslt-10/#strip>`_) remove whitespace-only text nodes from the *source* before any query runs, so
that ``position()``, ``last()``, and ``text()`` see the stripped tree the way the spec requires. turbohtml does this by
detaching the strippable text nodes from the source at the start of the transform and re-attaching them at the end, so
the caller's tree is a read-only participant that survives the call unchanged -- the same tree can drive many transforms
concurrently. A strip/preserve conflict on one element is resolved by import precedence then NameTest specificity, and
``xml:space="preserve"`` on an ancestor overrides both.

``xsl:import`` (`section 2.6.2 <https://www.w3.org/TR/xslt-10/#import>`_) does load other files, so the thin Python shim
resolves each ``href`` against the stylesheet's ``base_url`` and parses the imported sheets; the C engine then
deep-copies every sheet -- principal and imported -- into one private tree so all their declarations share a single atom
table, and walks them lowest import precedence first. Import precedence becomes the first key of the section 5.5
conflict resolution above. ``document()`` still loads nothing, to stay free of an arbitrary-URL fetch surface.

****************
 Where it stops
****************

Two spec features stay out of reach for want of a supporting layer, not by design. Locale-aware ``xsl:sort`` collation
(``lang="de"``) needs a Unicode collation/ICU layer turbohtml does not carry, so sorting is Unicode-codepoint order.
``id()`` over DTD-declared ID attributes needs a DTD layer the XML parser does not have. Both are marked ``xfail`` in
the conformance suite with that reason. Everything else in XSLT 1.0 is modeled: whitespace stripping, ``xsl:import``
with import precedence, ``xsl:attribute-set``, ``xsl:namespace-alias``, multi-level ``xsl:number``,
``cdata-section-elements``, the auto-selected html output method, simplified stylesheets, and ``xsl:fallback``. A result
tree fragment converts to its string value when used through XPath (the strict XSLT 1.0 rule), and ``xsl:copy-of`` of a
fragment variable copies its nodes.

*******************
 Conformance basis
*******************

The processor is validated against libxslt's own XSLT 1.0 Recommendation corpus -- the ``REC`` and ``REC2``
stylesheet/source/expected-output triples it ships, which are the worked examples from the spec run through the
reference implementation. ``tests/conformance/test_xslt_conformance.py`` runs turbohtml over every triple and asserts
byte-equal output (whitespace normalized per output method). Of the 79 cases, 76 pass exactly; the three that remain use
the locale-collation, DTD-id, and XPath namespace-axis layers turbohtml does not carry, and are marked ``xfail`` with
that reason. The corpus is the pinned ``tests/conformance/libxslt`` submodule.

For the XPath engine underneath, see :doc:`queries`; for a port from lxml, see :doc:`/migration/lxml`.
