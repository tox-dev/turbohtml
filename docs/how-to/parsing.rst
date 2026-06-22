########################
 Parse HTML into a tree
########################

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
        parser.feed("<p>caf".encode("utf-8"))
        parser.feed("é</p>".encode("utf-8"))
        document = parser.close()
    print(document.find("p").text)

.. testoutput::

    café

****************************************
 Inspect the parse errors of a document
****************************************

:func:`turbohtml.parse` recovers from malformed markup the way a browser does and records each WHATWG parse error it
recovered from on :attr:`~turbohtml.Document.errors`. Each :class:`~turbohtml.ParseError` carries the spec ``code`` and
the source position (1-based ``line``, 0-based ``col``); a well-formed document yields an empty list:

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
