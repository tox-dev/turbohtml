#############################
 The strict XML parsing mode
#############################

:func:`turbohtml.parse` builds the tree a browser would: the WHATWG tree-construction algorithm implies missing tags,
special-cases void elements, foster-parents misnested table content, and recovers from almost anything. That is exactly
wrong for XML, where the grammar is small and a single violation makes the document not XML at all.
:func:`turbohtml.parse_xml` is a separate front end over the same arena, atom, and node infrastructure that applies XML
1.0 productions instead of HTML quirks.

*****************************
 What changes from HTML mode
*****************************

The two modes differ wherever the HTML tree builder makes an assumption XML does not license:

- **No implied tags, no void elements.** Every element opens and closes explicitly; ``<br>`` is an ordinary element that
  must be written ``<br/>`` or ``<br></br>``. Nothing is inserted that the source did not write.
- **Self-closing is universal.** ``<x/>`` closes any element, not just the fixed HTML void set.
- **Case is significant.** ``<Note>`` and ``<note>`` are different elements, and ``</Note>`` does not close ``<note>``.
- **CDATA, PIs, and the XML declaration are first-class.** A ``<![CDATA[...]]>`` section becomes a
  :class:`~turbohtml.CData` node, a ``<?target data?>`` becomes a :class:`~turbohtml.ProcessingInstruction`, and a
  leading ``<?xml ...?>`` declaration is consumed without becoming a node.
- **Entities are the XML set.** Only ``amp``, ``lt``, ``gt``, ``quot``, ``apos``, and numeric character references
  resolve. An HTML entity like ``&nbsp;`` -- or any DTD-declared entity -- is an undefined-entity error, because the
  mode carries no entity table beyond the five the specification predefines.
- **A violation raises, it never recovers.** A mismatched or unclosed tag, an undeclared namespace prefix, an undefined
  entity, a duplicate attribute, or content outside the single root element stops the parse and raises
  :exc:`~turbohtml.HTMLParseError`, whose ``error`` is a :class:`~turbohtml.ParseError` with the code and source
  position. This mirrors ``lxml``'s default ``recover=False`` parser rather than the HTML path's error *collection* on
  :attr:`~turbohtml.Document.errors`.

********************
 Namespaces by name
********************

XML namespaces are a well-formedness concern here, not a rewrite. The parser tracks the ``xmlns:prefix`` declarations in
scope on the open-element stack, pushes each element's declarations before validating it, and reports an undeclared
prefix as an error; the reserved ``xml`` prefix is always in scope. What it does **not** do is resolve a prefix to a
URI. ``lxml`` stores a namespaced tag in Clark notation (``{urn:h}a``) and exposes an ``nsmap``; turbohtml keeps the
qualified name exactly as written (``h:a``) and leaves every ``xmlns``/``xmlns:prefix`` declaration as an ordinary
attribute on the element.

This is a deliberate fit to turbohtml's node model, whose element namespace is the fixed HTML/SVG/MathML enumeration the
HTML parser needs, not an open set of URIs. Keeping qualified names verbatim means an XML document round-trips through
the same serializer and navigates through the same :attr:`~turbohtml.Element.tag`/:attr:`~turbohtml.Element.attrs` API
as an HTML one, at the cost of not offering URI-keyed lookups. A pipeline that needs true namespace resolution,
DTD-declared entities, validation, or XSLT stays with ``lxml``; the goal of the XML mode is a well-formed,
dependency-free tree under the one node API, not a second full XML toolchain.

**************************
 Why a separate front end
**************************

The XML grammar shares nothing with the HTML tree-construction insertion modes, so threading an ``xml`` flag through the
WHATWG state machine would have meant a branch at every step for no shared logic. A standalone recursive-descent parser
that emits the same C node tree is smaller, keeps the HTML fast path untouched, and still reuses the arena allocator,
the interned tag and attribute atoms, and the zero-copy text spans -- an XML text run with no entities or line-ending
fixups points straight into the source buffer, exactly as the HTML parser's does.
