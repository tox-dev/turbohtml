#############
 Conformance
#############

.. currentmodule:: turbohtml.conformance

Parsing and conformance answer different questions. The WHATWG tree-construction algorithm is defined to *never fail*:
it recovers from any byte stream into a tree, recording each recovery as a :class:`~turbohtml.ParseError`. Conformance
is the other half -- the authoring requirements a document must meet even though it parsed cleanly. ``<img src=x>``
builds a perfectly good ``img`` element; it is still non-conforming, because the standard requires an ``alt`` attribute.
:func:`check` is that second surface: the rules the parser deliberately does not enforce.

******************************
 A severity model, not a bool
******************************

The reference validator for this space is the Nu Html Checker (`validator.nu <https://validator.nu>`_), and it does not
answer yes or no. It classifies each finding -- an ``error`` for a violated requirement, a ``warning`` for an authoring
recommendation, an ``info`` note for advice -- and the document is *invalid* exactly when at least one finding is an
error. :func:`check` adopts that shape. A :class:`ConformanceReport` is a ``valid`` verdict plus every
:class:`ConformanceMessage`, and each message carries a stable ``code`` (``"img-missing-alt"``) so a tool can match on
it, a :data:`Severity`, a human-readable message, and a source line and column. The verdict reads only the errors:
turning a warning into a hard failure is the caller's policy, made by inspecting :attr:`~ConformanceReport.warnings`,
not the checker's.

****************
 What it checks
****************

The rules cover the authoring mistakes that survive a parse, grouped the way the standard and WAI-ARIA 1.2 define them:

- **Text alternatives.** An ``img``, an ``area`` with an ``href``, or an ``input`` of type ``image`` without an ``alt``
  attribute (error).
- **Obsolete markup.** A non-conforming element (``center``, ``font``, ``marquee``, ...) or a presentational attribute
  (``align``, ``bgcolor``, ``link`` on ``body``, ``frame`` on ``table``, ...) the standard's obsolete-features table
  retired (error).
- **Identity.** A duplicate ``id`` (error).
- **ARIA.** A ``role`` whose value is not a defined non-abstract ARIA role (error), or one that merely restates the
  element's implicit role, such as ``<nav role=navigation>`` (warning).
- **Structure.** An empty heading, or a ``section`` or ``article`` with no heading and no accessible name (warning).
- **Document.** A whole document with no non-empty ``title`` (error) or no ``lang`` on ``html`` (warning). These apply
  only to a :class:`~turbohtml.Document`; a subtree runs the per-element rules alone.

****************
 Why the C core
****************

The check is a single pre-order pass over the tree the parser already built, and it runs entirely in the extension. It
is iterative, not recursive, so no depth of nesting can exhaust the C stack -- the checker needs no depth cap. Every
name test (an obsolete element, an ARIA role token, a presentational attribute) resolves against a static table by a
loop rather than a chain of comparisons, so a tag spelling the parser never interns is still caught and the cost stays a
handful of integer compares per node. The Python layer only shapes the C findings into the report records and derives
the verdict.

.. seealso::

    :doc:`/how-to/conformance` for the task recipes and :doc:`/reference/conformance` for the full API.
