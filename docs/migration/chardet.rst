##############
 From chardet
##############

.. package-meta:: chardet chardet/chardet

`chardet <https://chardet.readthedocs.io/>`_ is the pure-Python universal character encoding detector:
``chardet.detect(data)`` returns an ``{"encoding", "confidence", "language"}`` dict, ``detect_all`` ranks the
candidates, and ``UniversalDetector`` accumulates a stream chunk by chunk. This guide also covers `cchardet
<https://github.com/PyYoshi/cChardet>`_, whose ``cchardet.detect`` is the same call bound to the C uchardet engine.

***************
 Why turbohtml
***************

:func:`turbohtml.detect.detect` answers the same question from the C detector turbohtml already ships for
:func:`~turbohtml.parse`: a port of Firefox's `chardetng <https://github.com/hsivonen/chardetng>`_, run after the WHATWG
sniff (byte-order mark, then ``<meta>`` prescan), with the spec's windows-1252 fallback -- the algorithm a browser would
use on the same bytes. Certain input short-circuits before any scoring, so ASCII, valid UTF-8, and real web pages
resolve 50x to 2000x ahead of chardet's prober ensemble; declaration-less legacy single-byte text still runs about 3x
ahead. Both libraries decode a 15-sample multilingual differential correctly, though chardet often names a sibling or
superset (ISO-8859-7 for Greek windows-1253 bytes, GB18030 for GBK) where turbohtml reports the WHATWG encoding a
browser would pick.

.. bench-table::
    :file: bench/chardet.json

*************
 The renames
*************

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

**********
 cchardet
**********

The original ``cchardet`` package stopped at 2.1.7 (2021) and no longer compiles on Python 3.11+, where the
``longintrepr.h`` header it includes left the public C API; the maintained fork `faust-cchardet
<https://github.com/faust-streaming/cChardet>`_ keeps the ``import cchardet`` name alive. Both expose only ``detect``
and a ``UniversalDetector`` without ``detect_all``, and neither reports a language; the turbohtml calls above replace
either package unchanged. One workload keeps the C fork ahead: on CJK-heavy byte streams (the Shift_JIS row, 22x) its
uchardet engine outruns turbohtml, whose CJK scoring drives a CPython incremental codec per candidate; turbohtml leads
on the other rows.

**********
 Pitfalls
**********

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
- A UTF-8 byte-order mark comes back as ``UTF-8``, not chardet's ``UTF-8-SIG``; decode with the ``utf-8-sig`` codec when
  you need the mark stripped.
- The candidate set is chardetng's: UTF-8, ISO-2022-JP, five CJK encodings, and 19 single-byte encodings. chardet's
  extras outside that set (UTF-16/32 without a mark, MacCyrillic, TIS-620, Johab) resolve to the closest WHATWG
  candidate instead.
