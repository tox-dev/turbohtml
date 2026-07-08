########################
 The event-driven parse
########################

:mod:`turbohtml.saxparse` parses a document into a stream of events -- start tag, end tag, characters, comment, doctype,
processing instruction -- instead of a tree. It sits between the two surfaces you already know: lower level than
:func:`turbohtml.parse`, which hands back a navigable document, and higher level than :func:`turbohtml.tokenize`, which
hands back raw tokens that know nothing about tree structure. This note explains what "higher level than tokens" buys
you and what the event model does and does not promise about memory.

********************
 Events, not tokens
********************

A token stream is the literal markup: a ``<td>`` start-tag token appears exactly where the source wrote it, whether or
not that is a legal place for a cell. The SAX events are the tree the WHATWG algorithm *builds* from those tokens. When
the source opens a table cell with no table, row, or body around it, the tree builder inserts the implied ``<html>``,
``<head>``, ``<body>``, ``<tbody>``, and ``<tr>``, and foster-parents stray text out ahead of the table; the event
stream carries all of that. Every construct the standard error-recovery rules produce -- implied tags, auto-closed
paragraphs and list items, foster parenting, the adoption agency's re-nesting of misplaced formatting elements -- is
reflected, because the events come from the same C tree builder :func:`turbohtml.parse` uses. This is the one thing
:class:`python:html.parser.HTMLParser` cannot give you: its callbacks fire on tags as written, and it is documented as
not HTML5-conformant, so misnested and malformed input diverges from what a browser would do.

*******************************
 What "no tree" actually means
*******************************

The promise is that you never receive a tree and never build a Python object per node.
:func:`turbohtml.saxparse.sax_parse` and :func:`turbohtml.saxparse.iter_events` create no :class:`turbohtml.Element`,
:class:`turbohtml.Text`, or any other node wrapper; the walk reads the C nodes directly and emits one event object at a
time, which your handler consumes and drops. A one-pass extraction -- collect the links, count the headings, pull the
title -- therefore runs without ever holding a document-sized graph of Python objects, and the moment the parse ends the
working memory is released. Against :func:`turbohtml.parse`, whose returned document keeps the whole tree resident for
as long as you hold it, that is the win: the object graph you never build and the tree you never keep.

************************************
 Why this is not an O(depth) stream
************************************

It is tempting to expect the streaming, low-memory model of a pull tokenizer: memory proportional to how deeply elements
nest, not to how large the document is. A spec-correct HTML tree builder cannot offer that, and the reason is intrinsic
to the algorithm rather than to this implementation.

The WHATWG construction rules reach backwards. The adoption agency, triggered by a misnested formatting element's end
tag, moves and re-parents nodes that were emitted long before -- their subtrees are relocated into a fresh clone.
Adjacent character data is coalesced into a single text node, so a run of text stays an open append target until a
non-text sibling follows it. Foster parenting merges fostered text with an existing sibling before the table. In every
one of these cases the builder must still be able to touch a node the walk has already reported. A parser that freed
each subtree the instant it emitted its end event would corrupt itself the first time one of these rules fired. So the
working tree is retained until the parse completes, then freed in one shot; peak memory is proportional to the document,
the same as :func:`turbohtml.parse`.

Streaming parsers that genuinely run in O(depth) -- ``html.parser``, expat, Go's ``html.Tokenizer`` -- achieve it by
*not* being tree builders: they keep only a stack of open element names and accept that retroactive reorderings simply
do not happen (or are the caller's problem). turbohtml takes the other side of that trade: it keeps the working tree so
the events can be spec-correct, and spends the memory to buy the correctness. The events are cheaper than a retained DOM
and far cheaper than a Python object graph; they are not a way to parse a document larger than memory. When you need
that, tokenize instead and do your own bookkeeping, or accept ``html.parser``'s non-conformant recovery.

*******
 Speed
*******

Tokenization, tree construction, and the document-order walk all run in the C extension; the only work that crosses into
Python is the per-event dispatch. Driving a page through :func:`turbohtml.saxparse.sax_parse` with a counting handler
runs several times faster than the same counting handler on :class:`python:html.parser.HTMLParser`, whose tokenizer and
entity handling are pure Python -- and you get the corrected tree the standard library never reconstructs. The ``sax``
benchmark in the CodSpeed suite tracks this against ``html.parser`` on real pages.
