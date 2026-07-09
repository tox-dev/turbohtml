########################
 Parse HTML into a tree
########################

Turn markup into a navigable tree with :func:`turbohtml.parse`: parse a whole document, feed one arriving in chunks, or
parse a fragment in a given context.

*************************************
 Parse a document arriving in chunks
*************************************

When a document arrives over a stream you do not have to buffer the whole thing before parsing. Feed each chunk to an
:class:`turbohtml.IncrementalParser` and call ``close()`` for the finished :class:`~turbohtml.Document`; the parser
holds only the bytes it has not yet consumed, never the whole source, and the result is identical to parsing the joined
string with :func:`turbohtml.parse`:

.. testcode::

    parser = turbohtml.IncrementalParser()
    for chunk in ("<ul><li>on", "e<li>two</", "ul>"):
        parser.feed(chunk)
    document = parser.close()
    print([item.text for item in document.find_all("li")])

.. testoutput::

    ['one', 'two']

``feed`` also accepts ``bytes``: a chunk is decoded with the parser's ``encoding`` (``utf-8`` by default), and a
multi-byte character split across two chunks is held back until the rest of its bytes arrive. As a context manager the
parser releases its work-in-progress when the block exits, so you can stop early without leaking the partial parse:

.. testcode::

    with turbohtml.IncrementalParser(encoding="utf-8") as parser:
        parser.feed(b"<p>caf")
        parser.feed("é</p>".encode())
        document = parser.close()
    print(document.find("p").text)

.. testoutput::

    café

****************************************
 Inspect the parse errors of a document
****************************************

:func:`turbohtml.parse` recovers from malformed markup the way a browser does and records each WHATWG parse error it
recovered from on :attr:`~turbohtml.Document.errors`: every tokenizer error the spec names, plus the preprocessing
errors a control, noncharacter, or surrogate code point raises by being in the input at all, in source order. Each
:class:`~turbohtml.ParseError` carries the spec ``code`` and the source position (1-based ``line``, 0-based ``col``); a
well-formed document yields an empty list:

.. testcode::

    import turbohtml

    document = turbohtml.parse("<a b b>")
    for error in document.errors:
        print(f"{error.code} at {error.line}:{error.col}")

.. testoutput::

    duplicate-attribute at 1:6

To fail instead of recover (in a linter or a strict ingest pipeline), pass ``strict=True`` and catch
:class:`~turbohtml.HTMLParseError`, whose ``error`` attribute is the first :class:`~turbohtml.ParseError`:

.. testcode::

    try:
        turbohtml.parse("<!DOCTYPE", strict=True)
    except turbohtml.HTMLParseError as exception:
        print(exception.error.code)

.. testoutput::

    eof-in-doctype

Gate a scrape on this list rather than on the shape of the tree. A page that raised nothing last week and now raises
``eof-in-tag`` arrived truncated; one raising ``control-character-in-input-stream`` came back under the wrong encoding.
Read it with :attr:`~turbohtml.Document.encoding_confidence` to tell a page that named its encoding from one the sniff
guessed at:

.. testcode::

    page = turbohtml.parse(b"<p>caf\xe9")
    print(page.encoding, page.encoding_confidence, [error.code for error in page.errors])

.. testoutput::

    windows-1252 tentative []

************************
 Parse an HTML fragment
************************

To parse markup that belongs inside a specific element (a table row, an SVG subtree), use
:func:`turbohtml.parse_fragment` with the context tag. It returns that context :class:`~turbohtml.Element` with the
parsed nodes as its children, applying the same insertion rules the element would impose in a full document:

.. testcode::

    row = turbohtml.parse_fragment("<td>a<td>b", "tr")
    print([cell.text for cell in row.find_all("td")])
    print(row.html)

.. testoutput::

    ['a', 'b']
    <tr><td>a</td><td>b</td></tr>

**************************************
 Reproduce the scripting-enabled tree
**************************************

By default turbohtml parses with the WHATWG scripting flag off, so ``<noscript>`` content is markup you can navigate:

.. testcode::

    document = turbohtml.parse("<noscript><a href='/no-js'>plain link</a></noscript>")
    print(document.find("a").text)

.. testoutput::

    plain link

A scripting browser instead treats ``<noscript>`` as a raw-text element -- its content is one text run, never parsed as
markup. Pass ``scripting=True`` to build that tree; the inner tags become literal text and serialize back unescaped:

.. testcode::

    document = turbohtml.parse("<noscript><a href='/no-js'>plain link</a></noscript>", scripting=True)
    noscript = document.find("noscript")
    print(noscript.text)
    print(noscript.html)

.. testoutput::

    <a href='/no-js'>plain link</a>
    <noscript><a href='/no-js'>plain link</a></noscript>

*********************************
 Find where an element came from
*********************************

Every parsed element records where its start tag began, so an error report or linter can point back at the source. Read
:attr:`~turbohtml.Node.source_line` (1-based), :attr:`~turbohtml.Node.source_col` (0-based), or the
:attr:`~turbohtml.Node.position` pair, the same convention as :meth:`python:html.parser.HTMLParser.getpos` and
``lxml``'s ``sourceline``:

.. testcode::

    doc = turbohtml.parse("<ul>\n  <li>first</li>\n  <li>second</li>\n</ul>")
    for item in doc.find_all("li"):
        print(item.text, item.position)

.. testoutput::

    first (2, 2)
    second (3, 2)

An element with no place in the source (a node built by hand, or an implied ``html``/``head``/``body``) reads ``None``.
Pass ``positions=False`` to :func:`turbohtml.parse` to skip the tracking entirely (a small memory and speed saving),
after which every accessor reads ``None``:

.. testcode::

    print(turbohtml.parse("<p>x</p>", positions=False).find("p").source_line)
    print(turbohtml.Element("div").position)

.. testoutput::

    None
    None

*********************************************
 Slice the source of every tag and attribute
*********************************************

For a linter, a formatter, or a source-mapping tool, the start-tag line and column are not enough: you need the exact
span of the tag, of its closing tag, and of each attribute. Pass ``source_locations=True`` to :func:`turbohtml.parse`
and read :attr:`~turbohtml.Node.source_location`, the :class:`~turbohtml.SourceLocation` record parse5 exposes as
``sourceCodeLocationInfo``. Every span is a :class:`~turbohtml.SourceSpan` of start/end line, column, and code-point
offset, and the half-open ``[start_offset, end_offset)`` slices the construct straight out of the source:

.. testcode::

    source = '<a href="/x" title="home">go</a>'
    link = turbohtml.parse(source, source_locations=True).find("a")
    location = link.source_location
    start, end = location.start_tag, location.end_tag
    print(source[start.start_offset : start.end_offset])
    print(source[end.start_offset : end.end_offset])
    for name, span in location.attrs.items():
        print(name, "->", source[span.start_offset : span.end_offset])

.. testoutput::

    <a href="/x" title="home">
    </a>
    href -> href="/x"
    title -> title="home"

An element the source never closed -- a void element, a self-closed one, or one closed implicitly or at end of input --
has ``end_tag`` ``None``. Tracking is off by default; ``source_locations=True`` implies ``positions=True``, so
``source_line`` and ``position`` stay available beside the spans.
