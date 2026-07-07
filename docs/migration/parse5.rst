#############
 From parse5
#############

`parse5 <https://github.com/inikulin/parse5>`_ is the reference JavaScript WHATWG parser -- the tree builder behind
jsdom, Angular, and much of the Node HTML ecosystem. It is unusual among the libraries here in being JavaScript rather
than Python, so this guide is a cross-language reference: it maps the parse5 API, and its ``sourceCodeLocationInfo``
source-location model in particular, onto turbohtml for teams moving an HTML pipeline from Node to Python.

Both build the same WHATWG tree from the same algorithm, so the port is mechanical. Where parse5 hands back a plain
object tree and leaves querying, mutation, and serialization to other packages (``@parse5/tools``, ``dom-serializer``,
your own walks), turbohtml keeps you inside one typed :class:`~turbohtml.Document` for all of it.

*********************
 turbohtml vs parse5
*********************

.. list-table::
    :header-rows: 1
    :widths: 16 42 42

    - - Dimension
      - turbohtml
      - parse5
    - - Language
      - Python, over a C engine
      - JavaScript (Node/browser)
    - - Scope
      - Parse, query, mutate, and serialize in one library
      - Parse and serialize; querying and mutation are your own walks or extra packages
    - - Source locations
      - :attr:`~turbohtml.Node.source_location` on each element under ``parse(source_locations=True)``
      - ``sourceCodeLocation`` on each node under ``{sourceCodeLocationInfo: true}``
    - - Querying
      - CSS :meth:`~turbohtml.Node.select`, XPath 1.0 :meth:`~turbohtml.Node.xpath`, the
        :meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.find_all` grammar
      - none built in; walk ``childNodes`` or add a package
    - - Typing
      - Fully type annotated with bundled stubs
      - TypeScript types shipped with the package
    - - Performance
      - Native C engine straight into the native tree; see the table below
      - JavaScript over the same algorithm

Source-location parity
======================

parse5's ``sourceCodeLocationInfo`` option is the model turbohtml's ``source_locations`` matches. Both attach, to each
element, the span of its start tag, its end tag, and each attribute; both count a 1-based line, a 0-based column, and a
code-point offset; and both leave the end tag absent when the source never closed the element. The field names differ
but line up one for one:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - parse5 ``sourceCodeLocation``
      - turbohtml :class:`~turbohtml.SourceLocation`
    - - ``loc.startTag`` (``{startLine, startCol, startOffset, endLine, endCol, endOffset}``)
      - ``location.start_tag`` (a :class:`~turbohtml.SourceSpan`)
    - - ``loc.endTag`` (``null`` when unclosed)
      - ``location.end_tag`` (``None`` when unclosed)
    - - ``loc.attrs[name]``
      - ``location.attrs[name]``
    - - ``span.startOffset`` / ``span.endOffset``
      - ``span.start_offset`` / ``span.end_offset``
    - - ``span.startLine`` / ``span.startCol``
      - ``span.start_line`` / ``span.start_col``

One difference: parse5 also fills a ``sourceCodeLocation`` on text, comment, and document-fragment nodes, and a
whole-element span (its ``startOffset``/``endOffset`` across children). turbohtml scopes
:attr:`~turbohtml.Node.source_location` to elements and reports ``None`` for other node types; a text run's own offsets
are not exposed. Read a node's :attr:`~turbohtml.Node.source_line`/:attr:`~turbohtml.Node.source_col` for the coarse
position that stays available on every parse.

Performance
===========

turbohtml records the same spans in its C engine, so parsing the same document with locations on runs 1.5x to 3.5x
faster than parse5 measured in-process (Node startup excluded):

.. bench-table::
    :file: bench/parse5.json

****************
 How to migrate
****************

.. code-block:: javascript

    // parse5
    import { parse } from "parse5";

    const document = parse(html, { sourceCodeLocationInfo: true });
    const el = document.childNodes[1]; // walk childNodes by hand
    const loc = el.sourceCodeLocation;
    html.slice(loc.startTag.startOffset, loc.startTag.endOffset);
    html.slice(loc.attrs.id.startOffset, loc.attrs.id.endOffset);

The turbohtml port parses the same way, then reads the span through :attr:`~turbohtml.Node.source_location` and slices
with plain offsets:

.. testcode::

    from turbohtml import parse

    source = '<html><body><div id="x">y</div></body></html>'
    div = parse(source, source_locations=True).find("div")
    location = div.source_location
    print(source[location.start_tag.start_offset : location.start_tag.end_offset])
    print(source[location.attrs["id"].start_offset : location.attrs["id"].end_offset])

.. testoutput::

    <div id="x">
    id="x"

**********************
 Gotchas and pitfalls
**********************

- **Opt in explicitly.** parse5's ``sourceCodeLocationInfo`` defaults off; so does turbohtml's ``source_locations``.
  Pass ``parse(html, source_locations=True)`` -- reading :attr:`~turbohtml.Node.source_location` on a tree parsed
  without it returns ``None``, never raises.
- **Only elements carry the record.** parse5 attaches ``sourceCodeLocation`` to text and comment nodes too; turbohtml
  reports ``None`` for non-element nodes. Port any code that read a text node's offsets to work from its surrounding
  elements.
- **Offsets are code points, not UTF-16 units.** parse5's offsets index a JavaScript string (UTF-16 code units), so an
  astral character counts as two; turbohtml's offsets are Python code points, so it counts as one. Slice each library's
  offsets against its own string type and the results agree.
- **Newlines are normalized.** As in parse5, a ``\r\n`` collapses to one line break before the counters advance, so line
  and offset numbers match the normalized source rather than the raw bytes.
- **No pluggable tree adapter.** parse5's ``treeAdapter`` lets you build a custom node shape; turbohtml always builds
  its own :class:`~turbohtml.Document`. To feed another toolchain, serialize with :attr:`~turbohtml.Node.html` and
  reparse there.
