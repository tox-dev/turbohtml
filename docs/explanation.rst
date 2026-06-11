#############
 Explanation
#############

**************
 Why a C core
**************

Escaping and unescaping sit on hot paths: HTML output escaping runs on every rendered fragment, and unescaping runs on
every chunk of text an HTML parser emits. ``turbohtml`` implements both in C so they run several times faster than an
equivalent pure-Python implementation, with no change in behavior.

Measured on CPython 3.14 (a release build, via ``tox -e bench``) against :func:`python:html.escape` and
:func:`python:html.unescape`:

.. list-table::
    :header-rows: 1
    :widths: 12 34 14 14 12

    - - operation
      - input
      - turbohtml
      - stdlib
      - speedup
    - - ``escape``
      - plain prose, no specials
      - 0.35 µs
      - 2.23 µs
      - 6.3x
    - - ``escape``
      - typical HTML markup
      - 4.49 µs
      - 10.5 µs
      - 2.3x
    - - ``escape``
      - special-dense
      - 2.99 µs
      - 26.5 µs
      - 8.9x
    - - ``escape``
      - non-ASCII prose (UCS-2)
      - 0.92 µs
      - 1.88 µs
      - 2.0x
    - - ``escape``
      - astral text (UCS-4)
      - 2.58 µs
      - 2.65 µs
      - 1.0x
    - - ``unescape``
      - named references (dense)
      - 18.1 µs
      - 70.2 µs
      - 3.9x
    - - ``unescape``
      - numeric references (dense)
      - 4.16 µs
      - 76.8 µs
      - 18.5x
    - - ``unescape``
      - mixed named + numeric
      - 8.03 µs
      - 35.2 µs
      - 4.4x
    - - ``unescape``
      - prose, sparse references
      - 3.93 µs
      - 3.87 µs
      - ~1x
    - - ``unescape``
      - non-ASCII with references
      - 9.44 µs
      - 35.2 µs
      - 3.7x

``escape`` gains the most on text that needs little escaping (the SWAR scan skips eight safe bytes at a time);
``unescape`` gains the most on entity-heavy input, especially numeric references, where the standard library pays a
Python call per match. On mostly-plain text ``unescape`` ties :func:`python:html.unescape`, whose regex already
short-circuits and runs in C. Numbers vary with input and hardware; reproduce them with ``tox -e bench``.

Unlike a standard-library accelerator, ``turbohtml`` ships **only** the compiled implementation. :PEP:`399` requires a
pure-Python fallback only for the standard library; as a third-party package distributing per-interpreter wheels,
turbohtml has no need for one, which keeps the surface small.

*************************
 Word-at-a-time scanning
*************************

``escape`` spends most of its time confirming that a string contains nothing that needs escaping. For one-byte strings
it scans eight bytes at a time using SWAR ("SIMD within a register"): it loads eight bytes into a 64-bit integer and
tests every lane for ``&``, ``<``, ``>`` and the quotes with the `has-zero bit trick
<https://graphics.stanford.edu/~seander/bithacks.html#ZeroInWord>`_, advancing eight bytes whenever none match. When
nothing needs escaping the input is returned unchanged. Wider (UCS-2 / UCS-4) strings — see :PEP:`393` for CPython's
string representations — fall back to a straightforward scalar scan. This needs the `PyUnicode buffer API
<https://docs.python.org/3/c-api/unicode.html>`_, which is why turbohtml cannot use the :ref:`Limited API
<python:stable>`.

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
