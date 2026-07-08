##############
 Why a C core
##############

Speed comes first in turbohtml, ahead of ease of maintenance. The tokenizer, the WHATWG tree builder, the CSS and XPath
engines, escaping, and serialization run in C over a single arena that holds no Python objects; Python appears only at
the typed edge, where a thin facade wraps the nodes you touch. This page explains what the C core buys and how it is
laid out, then works through escaping and unescaping as the concrete case.

*********************
 One arena per parse
*********************

A parse allocates its whole tree from one bump-allocated arena: a growing block of memory the builder hands out by
advancing a pointer, never freeing an individual node. Every element, attribute, and run of text lives in that block as
a compact C record with integer offsets into the source, not as a Python object. The design follows the lexbor and
html5ever layouts turbohtml benchmarks against. Two properties fall out of it. Allocation is close to free, so building
a tree of a hundred thousand nodes costs one arena walk rather than a hundred thousand ``malloc`` calls. And the tree
frees in one step when the arena drops, so there is no per-node reference counting on the hot path.

The Python objects you handle are wrappers created lazily: a :class:`turbohtml.Node` materializes when you reach a node,
and it forwards to the arena record underneath. A tree you never walk into never allocates a wrapper. A read snapshots
the arena before it hands control to any Python callback, which is what lets a walk stay consistent under concurrent
edits (see :doc:`free-threading`).

***************
 Subsystem map
***************

The C sources under ``src/turbohtml/_c`` split by subsystem so each reads on its own, one concern at a time:

- ``core`` -- the arena, the atom table that interns tag and attribute names, and the shared string and vector
  primitives the rest build on.
- ``tokenizer`` -- the WHATWG tokenization state machine.
- ``dom`` -- the tree builder and the node model every query and edit runs against.
- ``query`` -- the CSS selector matcher, the ``find`` filter grammar, and the XPath 1.0 engine.
- ``css`` -- the CSS parser and the cascade that resolves a computed style.
- ``js`` -- the JavaScript tokenizer and minifier.
- ``clean`` -- the allowlist sanitizer, the autolinker, and the HTML minifier.
- ``extract`` -- main-content isolation, table reading, and structured-data collection.
- ``serialize`` -- HTML, Markdown, and plain-text output, plus canonicalization.
- ``encoding`` -- the byte sniffer and the incremental decoder.
- ``unicode`` -- normalization and the case-folding tables.
- ``url`` -- URL parsing and resolution.
- ``validate`` -- XSD, RELAX NG, and the HTML5 conformance rules.
- ``data`` -- the generated lookup tables (entity names, tag atoms, the public-suffix and IDNA data).

Escaping and unescaping sit on hot paths: HTML output escaping runs on every rendered fragment, and unescaping runs on
every chunk of text an HTML parser emits. ``turbohtml`` implements both in C so they run several times faster than an
equivalent pure-Python implementation, with no change in behavior.

The :doc:`/development/performance` page measures both against :func:`python:html.escape` and
:func:`python:html.unescape` over real corpora. ``escape`` gains the most on text that needs little escaping (the SIMD
scan classifies sixteen bytes at a time and copies clean stretches in bulk); ``unescape`` gains the most on entity-heavy
input, where the standard library pays a Python call per match. The gap is narrowest on tiny strings, where call
overhead dominates, and on special-dense markup, where both sides spend their time writing replacements.

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
