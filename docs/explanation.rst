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
:func:`python:html.escape` and :func:`python:html.unescape`. The multi-MiB inputs stream well past the CPU caches; the
book and spec cases are real documents (Project Gutenberg's *War and Peace*, the WHATWG HTML spec source) referenced as
git submodules. Reproduce with ``tox -e bench``:

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
      - 2.38 µs
      - 8.09 µs
      - 3.4x
    - - ``escape``
      - no-op prose (4 MiB)
      - 0.12 ms
      - 2.66 ms
      - 22.2x
    - - ``escape``
      - book text (3 MiB)
      - 0.72 ms
      - 2.80 ms
      - 3.9x
    - - ``escape``
      - book HTML (4 MiB)
      - 1.35 ms
      - 4.88 ms
      - 3.6x
    - - ``escape``
      - spec HTML, dense (4 MiB)
      - 5.27 ms
      - 13.3 ms
      - 2.5x
    - - ``escape``
      - UCS-2 plain (4 MiB)
      - 0.74 ms
      - 2.60 ms
      - 3.5x
    - - ``escape``
      - UCS-2 markup (4 MiB)
      - 3.44 ms
      - 11.5 ms
      - 3.3x
    - - ``escape``
      - UCS-4 plain (4 MiB)
      - 0.97 ms
      - 5.58 ms
      - 5.8x
    - - ``escape``
      - UCS-4 markup (4 MiB)
      - 4.08 ms
      - 20.3 ms
      - 5.0x
    - - ``unescape``
      - tiny plain (64 B)
      - 0.02 µs
      - 0.03 µs
      - 1.3x
    - - ``unescape``
      - medium dense refs (4 KiB)
      - 8.57 µs
      - 72.5 µs
      - 8.5x
    - - ``unescape``
      - numeric refs (4 KiB)
      - 5.24 µs
      - 81.1 µs
      - 15.5x
    - - ``unescape``
      - book HTML, real refs (4 MiB)
      - 2.80 ms
      - 8.96 ms
      - 3.2x
    - - ``unescape``
      - escaped book HTML (5 MiB)
      - 2.10 ms
      - 21.2 ms
      - 10.1x
    - - ``unescape``
      - dense refs (4 MiB)
      - 10.4 ms
      - 78.5 ms
      - 7.6x
    - - ``unescape``
      - UCS-2 refs (4 MiB)
      - 2.78 ms
      - 19.4 ms
      - 7.0x

``escape`` gains the most on text that needs little escaping (the SIMD scan classifies sixteen bytes at a time and
copies clean stretches wholesale); ``unescape`` gains the most on entity-heavy input, where the standard library pays a
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
comparison directly into that special's output growth and sums the block branchlessly; the writing pass converts the
comparisons into a position bitmask so clean stretches are copied wholesale and only the matched bytes are rewritten.
When nothing needs escaping the input is returned unchanged. Wider (UCS-2 / UCS-4) strings — see :PEP:`393` for
CPython's string representations — pack four / two code points into a 64-bit SWAR word and probe all five special
characters in a single pass. ``unescape`` works the same way in reverse: it hops between ``&`` occurrences (``memchr``
on one-byte text) and bulk-copies the clean spans between references instead of inspecting every character. This needs
the `PyUnicode buffer API <https://docs.python.org/3/c-api/unicode.html>`_, which is why turbohtml cannot use the
:ref:`Limited API <python:stable>`.

*******************************
 Matching the standard library
*******************************

``turbohtml`` reproduces the behavior of :func:`python:html.escape` and :func:`python:html.unescape` exactly. ``escape``
uses the same replacements, including ``&#x27;`` for the single quote, and ``unescape`` applies the full `HTML5
character-reference rules <https://html.spec.whatwg.org/multipage/named-characters.html>`_: named references with
longest-prefix matching, numeric references, the Windows-1252 remaps, and the invalid code-point handling that maps to
``U+FFFD`` or the empty string. The test suite checks the C output against the standard library over a large fuzzed
corpus.

************************
 A spec-exact tokenizer
************************

:func:`turbohtml.tokenize` implements the `WHATWG HTML tokenization algorithm
<https://html.spec.whatwg.org/multipage/parsing.html#tokenization>`_ — the same state machine inside every browser —
rather than a regex approximation like :class:`python:html.parser.HTMLParser`. The C implementation mirrors the spec
state by state so the two can be read side by side, and it is validated against the shared `html5lib-tests
<https://github.com/html5lib/html5lib-tests>`_ conformance suite that browsers and parser libraries validate against, at
all three input storage widths, once per input storage width, because the token stream must be invariant to how CPython
happens to store the string.

Two deliberate scope decisions keep the surface honest:

- The tokenizer is not a parser. It hands you the token stream; it does not build a tree, balance tags, or apply the
  tree-construction rules. The one tree-construction duty it takes on is content-model switching: after a start tag for
  ``script``, ``style``, ``title`` and the other raw-text elements, the element's contents tokenize as the spec requires
  (a ``<b>`` inside a script body is text, not a tag).
- Parse errors are recovered from, not reported. The spec defines a recovery transition for every error and the machine
  takes it, so malformed input produces the same tokens a browser would see; the error stream itself is not part of the
  API.

Where behavior could drift, it is pinned by more than the suite: the token stream is fuzz-compared against html5lib's
tokenizer, and source positions use the same 1-based-line, 0-based-column convention as :mod:`python:html.parser`, so
diagnostics line up with what the standard library would report.

****************************
 Tokenizing at native width
****************************

CPython stores a string at one of three widths (:PEP:`393`): one byte per character for Latin-1, two for the basic
multilingual plane, four beyond. The tokenizer keeps that representation end to end instead of widening everything to
UCS-4: the input buffer, accumulated text runs, tag names and attribute values all store code points at the narrowest
width their content needs, promoting only when a wider character actually arrives. The state-machine core is compiled
once per width — the same trick CPython's ``stringlib`` uses — so every read is direct indexing, and the plain-text
states bulk-scan to the next special character the way `html5ever <https://github.com/servo/html5ever>`_ does rather
than dispatching the state machine per character. For the ASCII documents that dominate real traffic, a text run travels
from input to the final ``str`` as one-byte copies.

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
      - 30.3 µs
      - 449 µs
      - 14.8x
      - 840 µs
      - 27.7x
    - - text-heavy prose
      - 0.55 µs
      - 2.92 µs
      - 5.3x
      - 149 µs
      - 273x
    - - attribute-heavy
      - 24.7 µs
      - 330 µs
      - 13.3x
      - 837 µs
      - 33.8x
    - - script-heavy
      - 13.0 µs
      - 162 µs
      - 12.5x
      - 526 µs
      - 40.5x
    - - entity-heavy
      - 22.3 µs
      - 205 µs
      - 9.2x
      - 1246 µs
      - 55.8x
    - - wpt tiny (0.6 kB)
      - 1.60 µs
      - 18.2 µs
      - 11.4x
      - 49 µs
      - 30.9x
    - - wpt small (4 kB)
      - 15.0 µs
      - 176 µs
      - 11.8x
      - 434 µs
      - 29.0x
    - - wpt medium (9.6 kB)
      - 34.9 µs
      - 376 µs
      - 10.8x
      - 1190 µs
      - 34.1x
    - - wpt large (92 kB)
      - 348 µs
      - 4250 µs
      - 12.2x
      - 9311 µs
      - 26.7x
    - - wpt CJK (124 kB)
      - 626 µs
      - 8926 µs
      - 14.3x
      - 22844 µs
      - 36.5x
    - - whatwg spec (235 kB)
      - 701 µs
      - 7838 µs
      - 11.2x
      - 20409 µs
      - 29.1x
    - - ecmascript spec (3 MB)
      - 7.08 ms
      - 57.9 ms
      - 8.2x
      - 192 ms
      - 27.1x
    - - whatwg spec source (7.9 MB)
      - 37.0 ms
      - 399 ms
      - 10.8x
      - 907 ms
      - 24.5x

The closest case is a document that is almost entirely a single text node, where the standard library's regex performs
one C scan and never really tokenizes; everywhere markup actually appears, the state machine is 8-15x faster. Numbers
vary with input and hardware; reproduce them with ``tox -e bench``.

****************
 Free-threading
****************

The extension holds no shared mutable state: inputs are immutable ``str`` objects, the lookup tables are read-only, and
each :class:`turbohtml.Tokenizer` owns its state machine outright, so tokenizers in different threads never contend. It
therefore declares free-threading support and a per-interpreter GIL on interpreters new enough to honor those slots, so
it does not force the global lock back on under a free-threaded build. As with any stateful object, feeding one
tokenizer from several threads at once needs synchronization on the caller's side. See the `free-threading extension
guide <https://docs.python.org/3/howto/free-threading-extensions.html>`_.
