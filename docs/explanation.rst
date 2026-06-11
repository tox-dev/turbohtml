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
      - 0.14 µs
      - 3.6x
    - - ``escape``
      - medium markup (4 KiB)
      - 2.54 µs
      - 8.17 µs
      - 3.2x
    - - ``escape``
      - no-op prose (4 MiB)
      - 0.12 ms
      - 2.80 ms
      - 23.3x
    - - ``escape``
      - book text (3 MiB)
      - 0.71 ms
      - 3.12 ms
      - 4.4x
    - - ``escape``
      - book HTML (4 MiB)
      - 1.38 ms
      - 5.06 ms
      - 3.7x
    - - ``escape``
      - spec HTML, dense (4 MiB)
      - 5.31 ms
      - 13.7 ms
      - 2.6x
    - - ``escape``
      - UCS-2 plain (4 MiB)
      - 0.74 ms
      - 2.67 ms
      - 3.6x
    - - ``escape``
      - UCS-2 markup (4 MiB)
      - 3.73 ms
      - 11.7 ms
      - 3.1x
    - - ``escape``
      - UCS-4 plain (4 MiB)
      - 1.52 ms
      - 6.09 ms
      - 4.0x
    - - ``escape``
      - UCS-4 markup (4 MiB)
      - 4.64 ms
      - 21.4 ms
      - 4.6x
    - - ``unescape``
      - tiny plain (64 B)
      - 0.02 µs
      - 0.03 µs
      - 1.4x
    - - ``unescape``
      - medium dense refs (4 KiB)
      - 14.8 µs
      - 74.4 µs
      - 5.0x
    - - ``unescape``
      - numeric refs (4 KiB)
      - 5.11 µs
      - 83.0 µs
      - 16.2x
    - - ``unescape``
      - book HTML, real refs (4 MiB)
      - 2.90 ms
      - 9.24 ms
      - 3.2x
    - - ``unescape``
      - escaped book HTML (5 MiB)
      - 6.10 ms
      - 22.2 ms
      - 3.6x
    - - ``unescape``
      - dense refs (4 MiB)
      - 17.0 ms
      - 80.4 ms
      - 4.7x
    - - ``unescape``
      - UCS-2 refs (4 MiB)
      - 5.55 ms
      - 20.7 ms
      - 3.7x

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
      - 47.3 µs
      - 451 µs
      - 9.5x
      - 850 µs
      - 18x
    - - text-heavy prose
      - 4.5 µs
      - 2.9 µs
      - 0.7x
      - 150 µs
      - 33x
    - - attribute-heavy
      - 33.5 µs
      - 315 µs
      - 9.4x
      - 846 µs
      - 25x
    - - script-heavy
      - 18.0 µs
      - 165 µs
      - 9.1x
      - 513 µs
      - 28x
    - - entity-heavy
      - 34.0 µs
      - 199 µs
      - 5.9x
      - 1240 µs
      - 36x
    - - wpt page (0.6 kB)
      - 2.5 µs
      - 18.7 µs
      - 7.5x
      - 50 µs
      - 20x
    - - wpt page (9.6 kB)
      - 47.7 µs
      - 380 µs
      - 8.0x
      - 1208 µs
      - 25x
    - - wpt page (92 kB)
      - 552 µs
      - 4178 µs
      - 7.6x
      - 9185 µs
      - 17x
    - - wpt page, CJK (124 kB)
      - 890 µs
      - 9067 µs
      - 10.2x
      - 23293 µs
      - 26x
    - - whatwg spec (235 kB)
      - 1167 µs
      - 8010 µs
      - 6.9x
      - 20481 µs
      - 18x

The one case the standard library wins — a document that is almost entirely a single text node — is where its regex
performs one C scan and never really tokenizes; everywhere markup actually appears, the state machine is 5–10x faster.
Numbers vary with input and hardware; reproduce them with ``tox -e bench``.

****************
 Free-threading
****************

The extension holds no shared mutable state: inputs are immutable ``str`` objects, the lookup tables are read-only, and
each :class:`turbohtml.Tokenizer` owns its state machine outright, so tokenizers in different threads never contend. It
therefore declares free-threading support and a per-interpreter GIL on interpreters new enough to honor those slots, so
it does not force the global lock back on under a free-threaded build. As with any stateful object, feeding one
tokenizer from several threads at once needs synchronization on the caller's side. See the `free-threading extension
guide <https://docs.python.org/3/howto/free-threading-extensions.html>`_.
