##############
 From chardet
##############

.. package-meta:: chardet chardet/chardet

`chardet <https://chardet.readthedocs.io/>`_ is the pure-Python universal character encoding detector, the tool most
projects reach for when they receive undeclared bytes from a file, a socket, or an HTTP body with no reliable
``Content-Type``. ``chardet.detect(data)`` returns an ``{"encoding", "confidence", "language"}`` dict, ``detect_all``
ranks the candidates, and ``UniversalDetector`` accumulates a stream chunk by chunk so a reader can stop as soon as the
verdict is settled. It ships an ensemble of probers (escape-sequence, multi-byte, and single-byte frequency models) that
vote on the most likely encoding, an approach inherited from Mozilla's original ``universalchardet``. It is a dependency
of ``requests`` and countless scrapers and ETL pipelines. This guide also covers `cchardet
<https://github.com/PyYoshi/cChardet>`_, whose ``cchardet.detect`` is the same call bound to the C ``uchardet`` engine.

turbohtml covers the same ground with :func:`turbohtml.detect.detect`, a standalone entry point over the C detector it
already ships for :func:`~turbohtml.parse`. It answers "what encoding are these bytes?" without an HTML parser in the
call path, and returns a typed :class:`~turbohtml.detect.EncodingMatch` in place of chardet's dict.

**********************
 turbohtml vs chardet
**********************

.. list-table::
    :header-rows: 1
    :widths: 18 41 41

    - - Dimension
      - turbohtml
      - chardet
    - - Scope
      - WHATWG-conformant detection: a byte-order-mark sniff, an HTML ``<meta>`` prescan of the first 1024 bytes, then
        the ``chardetng`` content detector with the spec's windows-1252 fallback -- the algorithm a browser runs on the
        same bytes.
      - Ensemble of probers voting on the most likely encoding; ignores HTML markup and never applies a browser's
        windows-1252 fallback.
    - - Feature breadth
      - ``detect``, ``detect_all``, an incremental :class:`~turbohtml.detect.Detector`, plus a frozen
        :class:`~turbohtml.detect.Detection` config for confidence floor, language hint, and allow/exclude constraints.
      - ``detect``, ``detect_all``, ``UniversalDetector`` with a ``lang_filter``; no allow/exclude set, no language
        hint.
    - - Performance
      - ASCII, valid UTF-8, and real web pages short-circuit before any scoring, resolving 50x to 2000x ahead; legacy
        single-byte text runs about 3x ahead. See the table below.
      - Prober ensemble runs every model on every input; no fast path for clean UTF-8 or ASCII.
    - - Typing
      - Fully typed: ``EncodingMatch``, ``Detection``, ``Detector`` are annotated dataclasses/classes with stubs.
      - Returns untyped ``dict``; type stubs are third-party.
    - - Dependencies
      - None beyond the turbohtml C extension.
      - Pure Python, no dependencies (``cchardet`` needs a C build).
    - - Maintenance
      - Actively developed alongside the parser; the detector is the same code the parser uses in production.
      - Maintained but slow-moving; the model set has been stable for years.

Feature overlap
===============

The detection surface ports one-to-one:

- ``chardet.detect(data)`` -> :func:`turbohtml.detect.detect`, same three fields as a typed record.
- ``chardet.detect_all(data)`` -> :func:`turbohtml.detect.detect_all`, ranked candidates best first.
- ``UniversalDetector()`` with ``feed`` / ``close`` / ``reset`` / ``done`` / ``result`` ->
  :class:`turbohtml.detect.Detector` with the same five members.
- ``UniversalDetector(lang_filter=...)`` -> a :class:`~turbohtml.detect.Detection` ``allowed`` frozenset of the WHATWG
  encoding names to keep.
- chardet's implicit 0.2 minimum confidence -> :meth:`Detection.chardet() <turbohtml.detect.Detection.chardet>`.
- ``cchardet.detect(data)`` (including the maintained ``faust-cchardet`` fork) -> :func:`turbohtml.detect.detect`.

What turbohtml adds
===================

- HTML ``<meta>`` charset awareness: a declaration in the first 1024 bytes wins per the WHATWG prescan, so detection
  agrees with what the browser and :func:`turbohtml.parse` would decode. chardet and cchardet ignore markup entirely.
- Browser-faithful results: turbohtml reports the WHATWG canonical encoding a browser would pick, where chardet often
  names a sibling or superset (ISO-8859-7 for Greek windows-1253 bytes, GB18030 for GBK).
- A frozen :class:`~turbohtml.detect.Detection` config with a language hint, an ``allowed`` set, and a mutually
  exclusive ``excluded`` set -- richer than chardet's single ``lang_filter``.
- Fully typed results: :class:`~turbohtml.detect.EncodingMatch` instead of an untyped dict.
- One detection path shared with parsing: standalone ``detect`` and ``parse(detect_encoding=True)`` always agree on the
  same bytes.

What chardet has that turbohtml does not
========================================

- A wider candidate set. turbohtml scores chardetng's list: UTF-8, ISO-2022-JP, five CJK encodings, and 19 single-byte
  encodings. chardet's extras outside that set (UTF-16/32 *without* a byte-order mark, MacCyrillic, TIS-620, Johab)
  resolve to the closest WHATWG candidate instead. No equivalent when you need one of those exact labels. A UTF-16 or
  UTF-32 stream that *does* carry a mark now reports its exact label (see below).
- A raw CJK speed edge on the C fork. On CJK-heavy byte streams (the Shift_JIS row), ``cchardet``'s uchardet engine
  outruns turbohtml, whose CJK scoring drives a CPython incremental codec per candidate. Workaround: keep
  ``faust-cchardet`` for that one workload if it dominates; turbohtml leads on every other row.

Performance
===========

.. bench-table::
    :file: bench/chardet.json

Certain input short-circuits before any scoring, so ASCII, valid UTF-8, and real web pages resolve 50x to 2000x ahead of
chardet's prober ensemble; declaration-less legacy single-byte text still runs about 3x ahead. Both libraries decode a
15-sample multilingual differential correctly, though chardet often names a sibling or superset where turbohtml reports
the WHATWG encoding a browser would pick. The one exception is CJK-heavy bytes, where ``cchardet``'s uchardet engine
leads (the Shift_JIS row, 22x); turbohtml leads on the other rows.

****************
 How to migrate
****************

Swap the import and read the fields off the typed record instead of the dict:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `chardet <https://chardet.readthedocs.io/>`__
      - turbohtml
    - - ``chardet.detect(data)``
      - :func:`~turbohtml.detect.detect`
    - - ``chardet.detect_all(data)``
      - :func:`~turbohtml.detect.detect_all`
    - - ``UniversalDetector()`` / ``feed`` / ``close`` / ``reset`` / ``done`` / ``result``
      - :class:`~turbohtml.detect.Detector` with the same five members
    - - ``UniversalDetector(lang_filter=LanguageFilter.CJK)``
      - ``Detection(allowed=frozenset({"gbk", "big5", "shift_jis", "euc-jp", "iso-2022-jp", "euc-kr"}))``
    - - the implicit 0.2 minimum confidence
      - ``Detection.chardet()``
    - - ``cchardet.detect(data)``
      - :func:`~turbohtml.detect.detect`

The dict becomes a typed :class:`~turbohtml.detect.EncodingMatch` with the same three fields:

.. code-block:: python

    # chardet
    import chardet

    guess = chardet.detect(data)  # {"encoding": ..., "confidence": ..., "language": ...}

    # turbohtml
    from turbohtml.detect import detect

    match = detect(data)  # EncodingMatch(encoding=..., confidence=..., language=...)

.. testcode::

    from turbohtml.detect import detect

    match = detect("Привет мир, как дела".encode("cp1251"))
    print(match.encoding, match.language)

.. testoutput::

    windows-1251 Russian

The maintained ``cchardet`` story is a footnote: the original package stopped at 2.1.7 (2021) and no longer compiles on
Python 3.11+, where the ``longintrepr.h`` header it includes left the public C API. The fork `faust-cchardet
<https://github.com/faust-streaming/cChardet>`_ keeps the ``import cchardet`` name alive. Both expose only ``detect``
and a ``UniversalDetector`` without ``detect_all``, and neither reports a language; the turbohtml calls above replace
either package unchanged.

**********************
 Gotchas and pitfalls
**********************

- Encoding names differ in spelling, not identity: turbohtml reports the WHATWG canonical name (``windows-1251``,
  ``Shift_JIS``), chardet its own casing (``Windows-1251``, ``CP932``), cchardet upper case. Every name turbohtml can
  detect is a valid :mod:`python:codecs` alias, so ``data.decode(match.encoding)`` works; only a ``<meta>``-declared
  ``x-user-defined`` has no stdlib codec.
- Confidence scales are not comparable across libraries. turbohtml's confidence is the candidate's share of the positive
  frequency scores (1.0 for a declaration or structural proof, 0.0 for the no-evidence windows-1252 fallback); do not
  port a chardet threshold number directly, use :meth:`Detection.chardet() <turbohtml.detect.Detection.chardet>` for its
  0.2 floor.
- turbohtml honors an HTML ``<meta>`` charset declaration in the first 1024 bytes, per the WHATWG prescan; chardet and
  cchardet ignore markup. Feed :class:`~turbohtml.detect.Detection` ``excluded`` constraints instead of re-sniffing when
  a declaration is known to lie.
- A byte-order mark reports the mark's own label and sets ``EncodingMatch.bom``: a UTF-8 mark comes back as
  ``UTF-8-SIG`` (chardet's spelling), and the UTF-16 and UTF-32 marks as ``UTF-16LE`` / ``UTF-16BE`` / ``UTF-32LE`` /
  ``UTF-32BE``. Decode with the matching codec (``utf-8-sig``, ``utf-16``, ``utf-32``) to strip the mark. The
  spec-locked :func:`~turbohtml.parse` sniff is unaffected -- it keeps the plain WHATWG name and treats ``FF FE 00 00``
  as UTF-16LE, so ``detect`` and ``parse(detect_encoding=True)`` agree on every input except a marked one.
- The candidate set is chardetng's: UTF-8, ISO-2022-JP, five CJK encodings, and 19 single-byte encodings. chardet's
  extras outside that set (UTF-16/32 *without* a mark, MacCyrillic, TIS-620, Johab) resolve to the closest WHATWG
  candidate instead.
