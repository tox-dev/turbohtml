############
 Validation
############

.. currentmodule:: turbohtml.validate

turbohtml validates XML against two schema languages -- XSD 1.0 and RELAX NG -- reusing the tree
:func:`turbohtml.parse_xml` already builds. The whole engine lives in the C core; :class:`XMLSchema` and
:class:`RelaxNG` are thin typed shims that compile a schema into a capsule once and hand each instance document to the
extension. This page explains the two designs and why they differ.

********************************
 Two engines, one instance tree
********************************

Both languages describe the same thing -- which elements, attributes, and text an XML document may contain -- but they
express it differently, so the validator uses the algorithm each language is built for rather than forcing one shape on
both.

**XSD** is a grammar of named declarations: global elements and types, referenced by name, with content models
(``sequence`` / ``choice`` / ``all`` and ``minOccurs`` / ``maxOccurs``) and a rich datatype-and-facet layer on the
leaves. The engine compiles a symbol table of the global declarations and then *interprets the schema tree directly*: it
walks the instance element against its declaration, matching a content model with an NFA-style reachable-position set so
repetition needs no backtracking, and resolving each leaf to a built-in datatype plus the facets gathered up its
restriction chain. Namespaces resolve from the in-scope ``xmlns`` declarations, so ``targetNamespace`` and
``elementFormDefault="qualified"`` validate correctly.

**RELAX NG** is a pattern algebra -- ``element``, ``attribute``, ``group``, ``choice``, ``interleave``, ``oneOrMore``,
``text``, ``data``, ``value``, ``list``, and ``ref`` -- and it is validated by James Clark's *derivative* algorithm
(`"An algorithm for RELAX NG validation" <https://www.thaiopensource.com/relaxng/derivative.html>`_). The schema
compiles to that algebra, and validation takes the *derivative* of the pattern with respect to each start tag,
attribute, text run, and end tag: the pattern that remains after consuming one piece of the document. This is what makes
``interleave`` fall out for free -- the derivative of ``interleave(p1, p2)`` over an element is the choice of advancing
either side -- with no backtracking and no combinatorial blow-up, because smart constructors absorb ``notAllowed`` and
``empty`` to keep the residual pattern small.

****************
 Why the C core
****************

The datatype and facet layer is where validation spends its time: every leaf value is checked against a lexical space
(is ``2020-13-40`` a date?) and then against its constraining facets (``minInclusive``, ``pattern``, ``length``, ...).
Doing that in the extension -- over the code-point buffers the parser already produced, with a compact Thompson-NFA
matcher for the ``pattern`` facet -- keeps a schema check close to the cost of the parse it follows, rather than a
second pass in Python. The recursion guards that protect the RELAX NG derivative from schemas whose refs recurse without
an element in between live there too, so an adversarial schema fails cleanly instead of overflowing the stack.

***********************
 What a result carries
***********************

:meth:`~XMLSchema.validate` returns a :class:`ValidationResult`: a ``valid`` flag and a tuple of
:class:`ValidationError` records. Each error carries a ``message``, the document-order ``path`` that locates the node, a
source ``line`` (``0`` when the tree carries no positions), and a coarse ``type`` -- ``"structure"`` for a content-model
or attribute violation, ``"datatype"`` for a value outside its type's lexical space, and ``"facet"`` for a value that
fails a constraining facet. A RELAX NG failure is localized to the single element whose content or name the schema
rejects, so the path points at the offending node rather than the document root.

.. seealso::

    :doc:`/how-to/validating` for the task recipes and :doc:`/reference/validate` for the full API.
