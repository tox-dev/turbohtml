#########################
 From charset-normalizer
#########################

.. package-meta:: charset-normalizer jawah/charset_normalizer

`charset-normalizer <https://charset-normalizer.readthedocs.io/>`_ is the encoding detector ``requests`` ships with in
place of chardet. It is a pure-Python library that decodes the input under every candidate codec CPython exposes, then
scores each decode by a "chaos" (mess) metric and a language-coherence metric, keeping the cleanest reading.
``from_bytes(data).best()`` returns the winning ``CharsetMatch``; ``from_path`` and ``from_fp`` do the same for files
and streams, a ``detect()`` shim mimics chardet's dict, and a ``normalizer`` console command exposes the whole thing
from the shell. Because it is pure Python with no compiled dependency, it installs anywhere and is the default fallback
detector across the ``requests`` ecosystem.

turbohtml covers the same detection job with :func:`turbohtml.detect.detect`, a C implementation of the WHATWG sniffing
pipeline (byte-order-mark and ``<meta>`` prescan) followed by Firefox's negative-matching ``chardetng`` scorer, so the
common case of declared or structurally certain input resolves without running a full scoring sweep.

*********************************
 turbohtml vs charset-normalizer
*********************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - charset-normalizer
    - - Scope
      - Encoding detection as part of a WHATWG HTML engine; BOM + ``<meta>`` prescan then ``chardetng`` scoring
      - Standalone encoding detection for arbitrary bytes; decode-and-score over every CPython codec
    - - Feature breadth
      - ``detect``, ``detect_all``, streaming ``Detector``, allow/exclude and language constraints
      - Rich match objects (chaos, coherence, alphabets, multiple languages), file/stream helpers, CLI, explain logging
    - - Performance
      - C, short-circuits certain input in microseconds; 6x-306x ahead on declared/UTF-8/ASCII bytes (see table)
      - Pure Python, always runs its per-codec decode and scoring passes
    - - Typing
      - Fully typed; frozen ``EncodingMatch`` / ``Detection`` records with shipped stubs
      - Typed public API with inline annotations
    - - Dependencies
      - The turbohtml C extension (no third-party runtime deps)
      - Pure Python, zero runtime dependencies
    - - Maintenance
      - Active, part of the turbohtml project
      - Active, widely deployed as the ``requests`` chardet replacement

Feature overlap
===============

Portable one-to-one between the two libraries:

- Best-guess detection: ``from_bytes(data).best()`` maps to :func:`~turbohtml.detect.detect`.
- All ranked candidates: iterating ``from_bytes(data)`` maps to :func:`~turbohtml.detect.detect_all`.
- The chardet-compatible dict: ``charset_normalizer.detect(data)`` maps to :func:`~turbohtml.detect.detect` (read
  ``match.encoding``, ``match.confidence``, ``match.language``).
- Restricting the candidate set: ``cp_isolation=[...]`` maps to ``Detection(allowed=frozenset({...}))`` and
  ``cp_exclusion=[...]`` to ``Detection(excluded=frozenset({...}))``.
- Language of the winning model: ``best().language`` maps to :attr:`EncodingMatch.language
  <turbohtml.detect.EncodingMatch>`.
- The byte-order-mark flag: ``best().bom`` maps to :attr:`EncodingMatch.bom <turbohtml.detect.EncodingMatch>`. A mark
  reports the mark's own label -- ``UTF-8-SIG`` for a UTF-8 mark and ``UTF-16LE`` / ``UTF-16BE`` / ``UTF-32LE`` /
  ``UTF-32BE`` for the UTF-16 and UTF-32 marks -- so ``data.decode(match.encoding)`` (or ``utf-8-sig`` / ``utf-16`` /
  ``utf-32``) strips it, matching charset-normalizer's mark-aware decode.
- A ``<meta>`` charset in the first bytes is honored by both (charset-normalizer's ``preemptive_behaviour``, on by
  default, and turbohtml's prescan).

What turbohtml adds
===================

- A WHATWG-conformant front end: the byte-order-mark check and ``<meta>`` prescan run before any statistical scoring,
  matching what an HTML parser actually does with the bytes.
- ``chardetng``'s negative matching: a single decode error disqualifies a candidate outright, then character-pair
  frequencies rank the survivors. On a 15-sample multilingual differential every turbohtml answer round-trips back to
  the source text; charset-normalizer garbles three (Czech ISO-8859-2 read as cp1250, Turkish windows-1254 as cp1252,
  KOI8-R Russian as shift_jis_2004).
- Microsecond short-circuits: declared or structurally certain input (BOM, ``<meta>``, valid UTF-8, ISO-2022-JP escape
  sequences, pure ASCII) resolves without the scoring sweep charset-normalizer always runs.
- A streaming :class:`~turbohtml.detect.Detector` (``feed`` / ``close`` / ``reset``, with a ``done`` early-stop flag)
  that mirrors chardet's ``UniversalDetector`` and always agrees with :func:`~turbohtml.detect.detect` of the
  concatenated bytes.
- A ``Detection.chardet()`` preset that reproduces chardet's 0.2 minimum-confidence behavior.
- A shell entry point: ``python -m turbohtml detect`` (installed as the ``turbohtml`` console script) prints the
  encoding of a file or stdin, covering the ``normalizer`` command's core job.

What charset-normalizer has that turbohtml does not
===================================================

- Arbitrary CPython codecs: charset-normalizer can propose any codec the interpreter ships (``big5hkscs``,
  ``shift_jis_2004``, and other exotic encodings). turbohtml detects only ``chardetng``'s web-focused set (UTF-8,
  ISO-2022-JP, five CJK encodings, 19 single-byte encodings). No equivalent for encodings outside that set.
- ``normalizer``'s richer report: its command tabulates chaos, coherence, and alphabets, where ``python -m turbohtml
  detect`` prints only the winning encoding name. Workaround: :func:`~turbohtml.detect.detect_all` from a script for the
  ranked candidates.
- ``explain=True`` verbose logging of the scoring decision. Workaround: :func:`~turbohtml.detect.detect_all` exposes the
  ranked candidates and their confidences as the introspection surface.
- File and stream helpers ``from_path`` / ``from_fp``. Workaround: read the bytes yourself and pass them to
  :func:`~turbohtml.detect.detect`, or feed chunks to :class:`~turbohtml.detect.Detector`.
- Rich per-match analysis on ``CharsetMatch``: chaos/coherence scores, alphabet listings, and multiple candidate
  languages. turbohtml reports a single ``confidence`` float and one ``language`` string per match. Workaround: none for
  the alphabet and multi-language breakdown.
- Chunked-scan tuning knobs (``steps``, ``chunk_size``). No equivalent; turbohtml scores over the whole input.

Performance
===========

.. bench-table::
    :file: bench/charset-normalizer.json

Declared or structurally certain input short-circuits, so ASCII, UTF-8, and real web pages resolve in microseconds -- 6x
to 306x ahead of charset-normalizer, which always runs its scoring passes. On declaration-less legacy bytes the two
trade wins (see the table).

****************
 How to migrate
****************

Swap the import and call :func:`~turbohtml.detect.detect` in place of ``from_bytes(...).best()``:

.. code-block:: python

    # charset-normalizer
    from charset_normalizer import from_bytes

    best = from_bytes(data).best()  # CharsetMatch | None

    # turbohtml
    from turbohtml.detect import detect

    match = detect(data)  # EncodingMatch

API mapping:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `charset-normalizer <https://charset-normalizer.readthedocs.io/>`__
      - turbohtml
    - - ``from_bytes(data).best()``
      - :func:`~turbohtml.detect.detect`
    - - ``from_bytes(data)`` (every candidate)
      - :func:`~turbohtml.detect.detect_all`
    - - ``charset_normalizer.detect(data)`` (the chardet shim)
      - :func:`~turbohtml.detect.detect`
    - - ``from_bytes(data, cp_isolation=[...])``
      - ``Detection(allowed=frozenset({...}))``
    - - ``from_bytes(data, cp_exclusion=[...])``
      - ``Detection(excluded=frozenset({...}))``
    - - ``best().language``
      - :attr:`EncodingMatch.language <turbohtml.detect.EncodingMatch>`
    - - ``str(best())`` (the decoded text)
      - ``data.decode(match.encoding)``
    - - ``best().could_be_from_charset`` (equal-fit codecs)
      - the runner-up entries of :func:`~turbohtml.detect.detect_all`

A worked round-trip:

.. testcode::

    from turbohtml.detect import detect

    raw = "Précédemment, la créativité française".encode("cp1252")
    match = detect(raw)
    print(match.encoding)
    print(raw.decode(match.encoding))

.. testoutput::

    windows-1252
    Précédemment, la créativité française

**********************
 Gotchas and pitfalls
**********************

- charset-normalizer's ``threshold`` bounds the *chaos* it tolerates (lower is stricter); :attr:`Detection.threshold
  <turbohtml.detect.Detection>` floors the *confidence* it requires (higher is stricter). The two numbers measure
  different things, so pick a new value rather than copying one across.
- Names differ in spelling: charset-normalizer reports Python codec names (``cp1251``), turbohtml the WHATWG canonical
  (``windows-1251``). Both decode through :mod:`python:codecs`, so downstream ``bytes.decode`` calls keep working.
- ``str(best())`` returns the decoded text directly; :func:`~turbohtml.detect.detect` returns only the encoding, so
  decode explicitly with ``data.decode(match.encoding)``.
- ``best()`` can be ``None`` when nothing scores; :func:`~turbohtml.detect.detect` always returns an
  :class:`~turbohtml.detect.EncodingMatch`, but its ``encoding`` is ``None`` for empty input or when every candidate is
  ruled out by ``threshold``, ``allowed``, or ``excluded``.
- charset-normalizer can propose any codec CPython ships; turbohtml's web-focused candidate set will not surface an
  exotic codec such as ``shift_jis_2004`` for plain legacy text, so results can differ on inputs outside common web
  encodings.
