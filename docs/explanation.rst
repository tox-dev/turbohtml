#############
 Explanation
#############

**************
 Why a C core
**************

Escaping and unescaping sit on hot paths: HTML output escaping runs on every rendered fragment, and unescaping runs on
every chunk of text an HTML parser emits. ``turbohtml`` implements both in C so they run several times faster than an
equivalent pure-Python implementation, with no change in behavior.

Measured with `pyperf <https://pyperf.readthedocs.io>`_ on CPython 3.14 (a release build, Apple M-series) against
:func:`python:html.escape` and :func:`python:html.unescape`. The multi-MiB inputs stream past the CPU caches; the book
and spec cases are real documents (Project Gutenberg's *War and Peace*, the WHATWG HTML spec source) referenced as git
submodules. Reproduce with ``tox -e bench``:

.. list-table::
    :header-rows: 1
    :widths: 12 34 14 14 12

    - - operation
      - input
      - turbohtml
      - stdlib
      - speedup
    - - ``escape``
      - tiny plain (64 B)
      - 0.04 µs
      - 0.11 µs
      - 2.9x
    - - ``escape``
      - medium markup (4 KiB)
      - 2.25 µs
      - 7.17 µs
      - 3.2x
    - - ``escape``
      - no-op prose (4 MiB)
      - 0.11 ms
      - 2.51 ms
      - 22.0x
    - - ``escape``
      - book text (3 MiB)
      - 0.66 ms
      - 2.56 ms
      - 3.9x
    - - ``escape``
      - book HTML (4 MiB)
      - 1.25 ms
      - 4.54 ms
      - 3.6x
    - - ``escape``
      - spec HTML, dense (4 MiB)
      - 4.93 ms
      - 12.8 ms
      - 2.6x
    - - ``escape``
      - UCS-2 plain (4 MiB)
      - 0.70 ms
      - 2.41 ms
      - 3.4x
    - - ``escape``
      - UCS-2 markup (4 MiB)
      - 3.33 ms
      - 10.9 ms
      - 3.3x
    - - ``escape``
      - UCS-4 plain (4 MiB)
      - 0.91 ms
      - 5.29 ms
      - 5.8x
    - - ``escape``
      - UCS-4 markup (4 MiB)
      - 3.95 ms
      - 19.3 ms
      - 4.9x
    - - ``unescape``
      - tiny plain (64 B)
      - 0.02 µs
      - 0.03 µs
      - 1.3x
    - - ``unescape``
      - medium dense refs (4 KiB)
      - 8.22 µs
      - 69.0 µs
      - 8.4x
    - - ``unescape``
      - numeric refs (4 KiB)
      - 5.83 µs
      - 78.7 µs
      - 13.5x
    - - ``unescape``
      - book HTML, real refs (4 MiB)
      - 2.44 ms
      - 7.87 ms
      - 3.2x
    - - ``unescape``
      - escaped book HTML (5 MiB)
      - 1.90 ms
      - 19.5 ms
      - 10.3x
    - - ``unescape``
      - dense refs (4 MiB)
      - 9.89 ms
      - 73.0 ms
      - 7.4x
    - - ``unescape``
      - UCS-2 refs (4 MiB)
      - 2.51 ms
      - 18.1 ms
      - 7.2x

``escape`` gains the most on text that needs little escaping (the SIMD scan classifies sixteen bytes at a time and
copies clean stretches in bulk); ``unescape`` gains the most on entity-heavy input, where the standard library pays a
Python call per match. The gap is narrowest on tiny strings, where call overhead dominates, and on special-dense markup,
where both sides spend their time writing replacements. Numbers vary with input and hardware; reproduce them with ``tox
-e bench``.

Unlike a standard-library accelerator, ``turbohtml`` ships **only** the compiled implementation. :PEP:`399` requires a
pure-Python fallback only for the standard library; as a third-party package distributing per-interpreter wheels,
turbohtml has no need for one, which keeps the surface small.

**************************
 Block-at-a-time scanning
**************************

``escape`` spends most of its time confirming that a string contains nothing that needs escaping. For one-byte strings
it classifies sixteen bytes at a time with SIMD (on arm64 NEON a single low-nibble table lookup plus one comparison
matches all five specials at once; on x86-64 SSE2 compares per special; elsewhere a 64-bit SWAR word applies the
`has-zero bit trick <https://graphics.stanford.edu/~seander/bithacks.html#ZeroInWord>`_). The sizing pass turns each
comparison into that special's output growth and sums the block without branches; the writing pass converts the
comparisons into a position bitmask, copies clean stretches in bulk, and rewrites only the matched bytes. When nothing
needs escaping, ``escape`` returns the input unchanged. Wider strings (UCS-2 / UCS-4; see :PEP:`393` for CPython's
string representations) pack four / two code points into a 64-bit SWAR word and probe all five special characters in a
single pass. ``unescape`` works the same way in reverse: it hops between ``&`` occurrences (``memchr`` on one-byte text)
and bulk-copies the clean spans between references instead of inspecting every character. This needs the `PyUnicode
buffer API <https://docs.python.org/3/c-api/unicode.html>`_, which is why turbohtml cannot use the :ref:`Limited API
<python:stable>`.

*******************************
 Matching the standard library
*******************************

``turbohtml`` reproduces the exact behavior of :func:`python:html.escape` and :func:`python:html.unescape`. ``escape``
uses the same replacements, including ``&#x27;`` for the single quote, and ``unescape`` applies the full `HTML5
character-reference rules <https://html.spec.whatwg.org/multipage/named-characters.html>`_: named references with
longest-prefix matching, numeric references, the Windows-1252 remaps, and the invalid code-point handling that maps to
``U+FFFD`` or the empty string. The test suite checks the C output against the standard library over a large fuzzed
corpus.

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
- The machine recovers from parse errors instead of reporting them. The spec defines a recovery transition for every
  error and the machine takes it, so malformed input produces the same tokens a browser would see; the error stream is
  not part of the API.

Where behavior could drift, more than the suite pins it: a fuzz comparison runs the token stream against html5lib's
tokenizer, and source positions use the same 1-based-line, 0-based-column convention as :mod:`python:html.parser`, so
diagnostics line up with what the standard library reports.

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

Measured on CPython 3.14 (a release build, via ``tox -e bench``) against :class:`python:html.parser.HTMLParser` driven
with no-op handlers and html5lib's pure-Python tokenizer, over synthetic cases and html5lib's benchmark corpus (a slice
of the WHATWG spec source plus web-platform-tests pages of varied sizes):

.. list-table::
    :header-rows: 1
    :widths: 26 14 16 12 14 12

    - - input
      - turbohtml
      - ``html.parser``
      - speedup
      - html5lib
      - speedup
    - - typical markup
      - 29.3 µs
      - 435 µs
      - 14.8x
      - 810 µs
      - 27.6x
    - - text-heavy prose
      - 0.54 µs
      - 2.81 µs
      - 5.2x
      - 143 µs
      - 263x
    - - attribute-heavy
      - 19.2 µs
      - 298 µs
      - 15.5x
      - 807 µs
      - 42.0x
    - - script-heavy
      - 12.1 µs
      - 156 µs
      - 12.9x
      - 488 µs
      - 40.4x
    - - entity-heavy
      - 20.4 µs
      - 197 µs
      - 9.6x
      - 1.20 ms
      - 58.9x
    - - wpt tiny (0.6 kB)
      - 1.41 µs
      - 17.5 µs
      - 12.5x
      - 47.7 µs
      - 34.0x
    - - wpt small (4 kB)
      - 12.1 µs
      - 165 µs
      - 13.7x
      - 422 µs
      - 34.8x
    - - wpt medium (9.6 kB)
      - 29.2 µs
      - 360 µs
      - 12.4x
      - 1.16 ms
      - 39.8x
    - - wpt large (92 kB)
      - 324 µs
      - 4.03 ms
      - 12.4x
      - 8.93 ms
      - 27.5x
    - - wpt CJK (124 kB)
      - 584 µs
      - 8.45 ms
      - 14.5x
      - 22.6 ms
      - 38.6x
    - - whatwg spec (235 kB)
      - 645 µs
      - 7.39 ms
      - 11.5x
      - 19.3 ms
      - 30.0x
    - - ecmascript spec (3 MB)
      - 5.88 ms
      - 55.0 ms
      - 9.4x
      - 181 ms
      - 30.8x
    - - whatwg spec source (7.9 MB)
      - 35.0 ms
      - 389 ms
      - 11.1x
      - 853 ms
      - 24.4x

The closest case is a document dominated by a single text node, where the standard library's regex performs one C scan
and never tokenizes; everywhere markup appears, the state machine is 9-16x faster. Numbers vary with input and hardware;
reproduce them with ``tox -e bench``.

*********************************
 A navigable tree without copies
*********************************

:func:`turbohtml.parse` runs the full `WHATWG tree-construction algorithm
<https://html.spec.whatwg.org/multipage/parsing.html#tree-construction>`_ on top of the tokenizer (the insertion modes,
the adoption agency, foreign content, and the error recovery a browser performs); the same `html5lib-tests
<https://github.com/html5lib/html5lib-tests>`_ tree-construction suite browsers use validates it. The result is the tree
a browser builds for the same bytes.

turbohtml builds the tree in C as a pointer-linked node graph in a single bump-allocated arena, the layout lexbor, Go's
``x/net/html``, and html5ever all converge on. It holds **no Python objects**. Element identity is an interned integer
atom, so every scope and category test in the algorithm is an integer compare rather than a string comparison, and
freeing the whole tree is one release of the arena rather than a per-node teardown.

turbohtml creates the Python :class:`~turbohtml.Node` objects only for the nodes you touch. Walking into a child or
sibling allocates one small wrapper that points at the existing arena node; it never copies the subtree. Text payloads
go further: a text node borrows a slice of the original input string and materializes into its own buffer only when you
first read its :attr:`~turbohtml.Node.text` or :attr:`~turbohtml.Text.data`. A private handle keeps the arena and the
input string alive for as long as any node reachable from them, so a node extracted from a document keeps working after
the document goes out of scope. Building the navigable :class:`~turbohtml.Document` costs about the same as building the
raw C tree, and the wrappers add cost only for the nodes you touch.

turbohtml parses faster than the C parsers lxml and selectolax, and 30 to 80 times faster than the pure-Python
BeautifulSoup and html5lib, while building the WHATWG tree that lxml's libxml2 does not. Measured on the same
web-platform-tests pages and specification sources via ``tox -e bench parse``:

.. list-table::
    :header-rows: 1
    :widths: 26 12 11 11 14 11

    - - input
      - turbohtml
      - lxml
      - selectolax
      - BeautifulSoup
      - html5lib
    - - wpt page (0.6 kB)
      - 1.3 µs
      - 3.3 µs
      - 6.8 µs
      - 61.6 µs
      - 101 µs
    - - wpt page (4 kB)
      - 10.6 µs
      - 26.7 µs
      - 42.1 µs
      - 443 µs
      - 616 µs
    - - wpt page (9.6 kB)
      - 25.4 µs
      - 72.6 µs
      - 107 µs
      - 849 µs
      - 1.44 ms
    - - wpt page (92 kB)
      - 268 µs
      - 629 µs
      - 920 µs
      - 15.5 ms
      - 17.0 ms
    - - wpt CJK (124 kB)
      - 584 µs
      - 8.45 ms
      - 14.5x
      - 22.6 ms
      - 38.6x
    - - whatwg spec (235 kB)
      - 645 µs
      - 7.39 ms
      - 11.5x
      - 19.3 ms
      - 30.0x
    - - ecmascript spec (3 MB)
      - 5.88 ms
      - 55.0 ms
      - 9.4x
      - 181 ms
      - 30.8x
    - - whatwg spec source (7.9 MB)
      - 35.0 ms
      - 389 ms
      - 11.1x
      - 853 ms
      - 24.4x

The node types are a small sealed hierarchy (:class:`~turbohtml.Document`, :class:`~turbohtml.Element`,
:class:`~turbohtml.Text`, :class:`~turbohtml.Comment`, :class:`~turbohtml.Doctype`) sharing the navigation defined on
:class:`~turbohtml.Node`. turbohtml models text as real child nodes (the WHATWG DOM shape) rather than the
``.text``/``.tail`` split lxml-style trees use, so a node's children are its text runs and elements interleaved in
document order. Each type sets ``__match_args__``, so structural pattern matching unpacks a node's defining field, and
node equality is identity over the underlying arena node, so two wrappers for the same element compare equal and hash
alike.

The traversal surface is small for this first increment: navigation (parents, siblings, the lazy
:attr:`~turbohtml.Node.descendants` and :attr:`~turbohtml.Node.ancestors` iterators), the sequence protocol over a
node's children, and :meth:`~turbohtml.Node.find` / :meth:`~turbohtml.Node.find_all` by tag and attributes. A
CSS-selector engine is a planned follow-up; the zero-copy node model is the foundation it will build on.

****************
 Free-threading
****************

The extension holds no shared mutable state: inputs are immutable ``str`` objects, the lookup tables are read-only, and
each :class:`turbohtml.Tokenizer` owns its state machine, so tokenizers in different threads never contend. The
extension declares free-threading support and a per-interpreter GIL on interpreters new enough to honor those slots, so
it does not force the global lock back on under a free-threaded build. As with any stateful object, feeding one
tokenizer from several threads at once needs synchronization on the caller's side. See the `free-threading extension
guide <https://docs.python.org/3/howto/free-threading-extensions.html>`_.
