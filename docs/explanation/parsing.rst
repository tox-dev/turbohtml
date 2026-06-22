#########################################
 Parsing: from bytes to a navigable tree
#########################################

A parse runs as a pipeline. Encoding detection turns input bytes into a ``str``, the tokenizer turns that string into a
WHATWG token stream, and the tree builder runs the tree-construction algorithm over those tokens to produce the DOM a
browser would build. The blue stages are input handling, the orange stages are the two spec state machines, and the
green stage is the navigable tree you query.

.. mermaid::

    flowchart LR
        bytes([input bytes]) --> enc[encoding detection]
        enc --> dec[decoded str]
        dec --> tok[tokenizer]
        tok --> tree[tree builder]
        tree --> dom([navigable DOM tree])

        classDef input fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
        classDef engine fill:#fff3e0,stroke:#e65100,color:#bf360c
        classDef output fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

        class bytes,enc,dec input
        class tok,tree engine
        class dom output

************************
 A spec-exact tokenizer
************************

:func:`turbohtml.tokenize` implements the `WHATWG HTML tokenization algorithm
<https://html.spec.whatwg.org/multipage/parsing.html#tokenization>`_ (the same state machine inside every browser)
rather than a regex approximation like :class:`python:html.parser.HTMLParser`. The C implementation mirrors the spec
state by state so the two read side by side, and the shared `html5lib-tests
<https://github.com/html5lib/html5lib-tests>`_ conformance suite that browsers and parser libraries validate against
checks it at all three input storage widths, once per width, because the token stream must be invariant to how CPython
stores the string.

Two decisions bound the tokenizer's scope:

- The tokenizer is not a parser. It hands you the token stream; it does not build a tree, balance tags, or apply the
  tree-construction rules. The one tree-construction duty it takes on is content-model switching: after a start tag for
  ``script``, ``style``, ``title`` and the other raw-text elements, the element's contents tokenize as the spec requires
  (a ``<b>`` inside a script body is text, not a tag).
- The machine recovers from parse errors rather than stopping on them. The spec defines a recovery transition for every
  error and the machine takes it, so malformed input produces the same tokens a browser would see. A duplicate attribute
  name is one such recovery: the spec keeps the first occurrence and drops the rest at tokenization, so ``<a href=x
  href=y>`` carries a single ``href`` of ``x`` everywhere it is observed: in the token, the parsed tree, and the
  serialized output alike. The recovery is silent in the token stream itself; the errors it papered over surface on
  :attr:`Document.errors <turbohtml.Document.errors>` when you :func:`~turbohtml.parse` (see below).

Where behavior could drift, more than the suite pins it: a fuzz comparison runs the token stream against html5lib's
tokenizer, and source positions use the same 1-based-line, 0-based-column convention as :mod:`python:html.parser`, so
diagnostics line up with what the standard library reports. For code that prefers callbacks to a token stream,
:class:`turbohtml.migration.stdlib.HTMLParser` re-exposes the stream through ``html.parser``'s subclass-and-override
surface, so an existing ``HTMLParser`` subclass migrates by swapping its base class.

****************************
 Tokenizing at native width
****************************

CPython stores a string at one of three widths (:PEP:`393`): one byte per character for Latin-1, two for the basic
multilingual plane, four beyond. The tokenizer keeps that representation end to end instead of widening everything to
UCS-4: the input buffer, accumulated text runs, tag names and attribute values all store code points at the narrowest
width their content needs, promoting only when a wider character arrives. turbohtml compiles the state-machine core once
per width (the same trick CPython's ``stringlib`` uses), so every read is direct indexing, and the plain-text states
bulk-scan to the next special character the way `html5ever <https://github.com/servo/html5ever>`_ does rather than
dispatching the state machine per character. For the ASCII documents that dominate real traffic, a text run travels from
input to the final ``str`` as one-byte copies.

The :doc:`/development/performance` page measures the tokenizer against :class:`python:html.parser.HTMLParser` and
html5lib's pure-Python tokenizer. The closest case is a document dominated by a single text node, where the standard
library's regex performs one C scan and never tokenizes; everywhere markup appears, the state machine is roughly ten
times faster.

*********************************
 A navigable tree without copies
*********************************

:func:`turbohtml.parse` runs the full `WHATWG tree-construction algorithm
<https://html.spec.whatwg.org/multipage/parsing.html#tree-construction>`_ on top of the tokenizer (the insertion modes,
the adoption agency, foreign content, and the error recovery a browser performs); the same `html5lib-tests
<https://github.com/html5lib/html5lib-tests>`_ tree-construction suite browsers use validates it. The result is the tree
a browser builds for the same bytes.

turbohtml builds the tree in C as a pointer-linked node graph in a single bump-allocated arena, the layout `lexbor
<https://lexbor.com>`_, Go's ``x/net/html``, and html5ever all converge on. It holds **no Python objects**. Element
identity is an interned integer atom, so every scope and category test in the algorithm is an integer compare rather
than a string comparison, and freeing the whole tree is one release of the arena rather than a per-node teardown.

turbohtml creates the Python :class:`~turbohtml.Node` objects only for the nodes you touch. Walking into a child or
sibling allocates one small wrapper that points at the existing arena node; it never copies the subtree. Text payloads
go further: a text node borrows a slice of the original input string and materializes into its own buffer only when you
first read its :attr:`~turbohtml.Node.text` or :attr:`~turbohtml.Text.data`. A private handle keeps the arena and the
input string alive for as long as any node reachable from them, so a node extracted from a document keeps working after
the document goes out of scope. Building the navigable :class:`~turbohtml.Document` costs about the same as building the
raw C tree, and the wrappers add cost only for the nodes you touch.

turbohtml parses faster than the C parsers lxml and `selectolax <https://github.com/rushter/selectolax>`_, and 30 to 80
times faster than the pure-Python BeautifulSoup and html5lib, while building the WHATWG tree that lxml's libxml2 does
not; the :doc:`/development/performance` page has the per-document figures.

The same tokenizer and tree builder also drive :class:`turbohtml.IncrementalParser`, the push form of
:func:`turbohtml.parse`. The tokenizer is resumable (it suspends mid-token when its input runs out) and reclaims the
prefix it has consumed on every chunk, so the only state the parser carries across a ``feed`` is the few insertion-mode
variables of the tree-construction loop, lifted out of the one-shot call into the parser. The input side is therefore
bounded no matter how long the stream: you never hold the whole source at once, the concrete win over ``parse`` for a
document that arrives over a socket or a file larger than the buffer you would otherwise join. ``bytes`` chunks decode
through a stateful incremental codec, so a multi-byte character split across a chunk boundary still decodes correctly.
The recovery a browser performs hides the spec's parse errors, but a linter or a strict pipeline still wants them.
:func:`~turbohtml.parse` records each error it recovers from on :attr:`Document.errors <turbohtml.Document.errors>`, a
list of :class:`~turbohtml.ParseError` carrying the spec error code and the source position (1-based line, 0-based
column, the same convention :class:`~turbohtml.Token` uses). Collection costs nothing on well-formed input (there are no
errors to record and the per-character paths are untouched), so it stays on by default rather than behind a flag; the
standalone :func:`~turbohtml.tokenize` and :class:`~turbohtml.Tokenizer`, which expose the raw stream, do not collect.
Pass ``strict=True`` to raise the first error as :class:`~turbohtml.HTMLParseError` (with the ``ParseError`` on its
``error`` attribute) instead of returning a recovered tree.
