#########################
 From charset-normalizer
#########################

.. package-meta:: charset-normalizer jawah/charset_normalizer

`charset-normalizer <https://charset-normalizer.readthedocs.io/>`_ is the detector ``requests`` ships with: it decodes
the input under candidate codecs and scores each result's "chaos" and language coherence. ``from_bytes(data).best()``
returns the winning ``CharsetMatch``, and a ``detect()`` shim mimics chardet's dict.

***************
 Why turbohtml
***************

:func:`turbohtml.detect.detect` runs Firefox's negative-matching design (`chardetng
<https://github.com/hsivonen/chardetng>`_) in C: a single decode error disqualifies a candidate and character-pair
frequencies score the survivors, after the WHATWG byte-order-mark and ``<meta>`` prescan steps. Declared or structurally
certain input short-circuits, so ASCII, UTF-8, and real web pages resolve in microseconds -- 6x to 306x ahead of
charset-normalizer, which always runs its scoring passes. On declaration-less legacy bytes the two trade wins (see the
table). On a 15-sample multilingual differential every turbohtml answer decodes the bytes back to the source text;
charset-normalizer garbles three, reading Czech ISO-8859-2 as cp1250, Turkish windows-1254 as cp1252, and KOI8-R Russian
as shift_jis_2004.

.. bench-table::
    :file: bench/charset-normalizer.json

*************
 The renames
*************

.. code-block:: python

    # charset-normalizer
    from charset_normalizer import from_bytes

    best = from_bytes(data).best()  # CharsetMatch | None

    # turbohtml
    from turbohtml.detect import detect

    match = detect(data)  # EncodingMatch

.. testcode::

    from turbohtml.detect import detect

    raw = "Précédemment, la créativité française".encode("cp1252")
    match = detect(raw)
    print(match.encoding)
    print(raw.decode(match.encoding))

.. testoutput::

    windows-1252
    Précédemment, la créativité française

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

**********
 Pitfalls
**********

- charset-normalizer's ``threshold`` bounds the *chaos* it tolerates (lower is stricter); :attr:`Detection.threshold
  <turbohtml.detect.Detection>` floors the *confidence* it requires (higher is stricter). The two numbers measure
  different things, so pick a new value rather than copying one across.
- Names differ in spelling: charset-normalizer reports Python codec names (``cp1251``), turbohtml the WHATWG canonical
  (``windows-1251``). Both decode through :mod:`python:codecs`, so downstream ``bytes.decode`` calls keep working.
- turbohtml honors a ``<meta>`` charset in the first 1024 bytes (charset-normalizer's ``preemptive_behaviour`` does the
  same and is on by default in both).
- There is no ``explain`` logging mode and no CLI; :func:`~turbohtml.detect.detect_all` is the introspection surface.
- charset-normalizer can propose any codec CPython ships; turbohtml detects chardetng's web-focused candidate set
  (UTF-8, ISO-2022-JP, five CJK, 19 single-byte encodings), which is why it cannot propose an exotic codec such as
  ``shift_jis_2004`` for plain legacy text.
