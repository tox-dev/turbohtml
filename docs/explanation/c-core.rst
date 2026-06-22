##############
 Why a C core
##############

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

##########################
 Block-at-a-time scanning
##########################

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
