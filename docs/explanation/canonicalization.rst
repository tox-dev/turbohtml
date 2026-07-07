######################
 Canonical XML (c14n)
######################

A digital signature signs bytes, but XML is written many ways that mean the same thing: attributes reorder, empty
elements self-close or not, whitespace and namespace declarations move around. `Canonical XML
<https://www.w3.org/TR/xml-c14n>`_ fixes a single byte string for a given document so that a signature computed by one
party verifies for another. :meth:`~turbohtml.Node.canonicalize` produces that byte string from a turbohtml tree.

*****************
 What c14n fixes
*****************

The canonical form pins every degree of freedom the serializer otherwise has:

- **Attribute order.** Namespace declarations come first (the default ``xmlns`` before prefixed ones), then the
  attributes sorted by namespace URI and, within a URI, by local name. A no-namespace attribute sorts before any
  namespaced one.
- **Namespace minimization.** A namespace declaration is emitted only where it enters scope, never redundantly
  redeclared on a descendant that inherits the same binding.
- **Empty elements.** Every element is a start-end pair -- ``<br></br>``, never ``<br/>`` -- so the self-closing choice
  cannot change the bytes.
- **Character references.** In text, only ``&``, ``<``, ``>`` and a carriage return take a reference; in an attribute
  value, ``&``, ``<``, ``"`` and the tab/newline/carriage-return controls do. Everything else, including non-ASCII, is
  emitted as literal UTF-8, so the output is a UTF-8 octet stream ready to hash.
- **Dropped constructs.** The document type declaration is dropped, and comments are dropped unless the with-comments
  variant is chosen.

****************************
 Inclusive versus exclusive
****************************

`Inclusive canonicalization <https://www.w3.org/TR/xml-c14n>`_ carries every in-scope namespace declaration down to a
subtree's apex, so a signed fragment stays verifiable even lifted out of its document. `Exclusive canonicalization
<https://www.w3.org/TR/xml-exc-c14n>`_ instead renders only the namespaces a subtree *visibly uses* -- its element and
attribute names -- so an ancestor declaration the subtree never references is dropped. Exclusive form is what enveloped
signatures use, because it stays stable when the signed element moves between documents that declare unrelated
namespaces around it. ``inclusive_ns_prefixes`` re-admits specific ancestor prefixes to the apex when a subtree needs
them despite not naming them.

**********************************
 Complete subtrees, not node-sets
**********************************

The c14n specifications are written over an arbitrary *node-set* -- a document with some nodes filtered out -- and their
subtlest rules (how an apex inherits ``xml:`` attributes from excluded ancestors) exist to handle that filtering.
turbohtml canonicalizes a **complete subtree**: a node and every descendant, nothing removed. For a complete subtree,
Canonical XML 1.0 and 1.1 are byte-for-byte identical apart from one detail -- 1.1 does not inherit ``xml:id`` onto the
apex, to avoid duplicating an id across signed fragments -- which is why the ``version`` knob rarely changes the output.
This matches how libxml2 (and therefore lxml) canonicalizes an element rather than a filtered node-set, so turbohtml's
bytes equal lxml's for the same infoset.

********************************
 An HTML infoset, canonicalized
********************************

turbohtml parses HTML, not arbitrary namespaced XML, so the namespace axis it canonicalizes is the one the WHATWG tree
carries: HTML elements are in no namespace, SVG and MathML carry their default namespace, and an ``xlink:`` attribute
binds the xlink prefix on the element that uses it. That is exactly the infoset the :class:`~turbohtml.Html`
``xml=True`` serialization emits, so canonicalizing a tree and canonicalizing a reparse of its XML serialization agree.
A document built on prefixes turbohtml's HTML model does not represent -- an arbitrary ``xmlns:foo`` binding -- is
outside this scope; parse it as XML and sign it with a full XML toolchain instead.

***********************
 Conformance validated
***********************

The output is checked byte-for-byte against `libxml2's own c14n test corpus
<https://github.com/GNOME/libxml2/tree/c8eaf2236ff16667970f96f3f01e119c99d38ab2/test/c14n>`_ -- the W3C c14n/c14n11
example documents and the xmldsig "merlin" interop vectors that libxml2 and lxml gate on -- in
``tests/conformance/test_c14n_conformance.py``. Of the 73 cases, the 9 that live in the HTML infoset (the c14n 1.0,
with-comments and 1.1 example documents free of node-set filtering, DTD typing and arbitrary namespaces) match exactly;
the other 64 exercise the XPath node-set subsetting, arbitrary XML namespaces and DTD attribute typing turbohtml scopes
out, and are recorded as expected mismatches with a per-case reason.
