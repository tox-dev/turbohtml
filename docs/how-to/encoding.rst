############################
 Handle character encodings
############################

Decode bytes of unknown or declared encoding the way a browser would, and inspect what the sniffer chose, with
:func:`turbohtml.parse` and :func:`turbohtml.detect.detect`.

************************************
 Parse bytes of an unknown encoding
************************************

:func:`turbohtml.parse` accepts ``bytes`` and runs the WHATWG encoding sniffing algorithm (a byte-order mark, then a
``<meta>`` declaration, defaulting to windows-1252). Pass ``encoding`` to override the sniff, and read
:attr:`~turbohtml.Document.encoding` for the WHATWG name that was resolved:

.. testcode::

    import turbohtml

    doc = turbohtml.parse(b'<meta charset="iso-8859-2"><p>\xe1</p>')
    print(doc.encoding)
    print(doc.find("p").text)

.. testoutput::

    ISO-8859-2
    á

************************************
 Detect an encoding without parsing
************************************

When you only need the encoding, say to decode a file or a response body, run the same sniff standalone with
:func:`turbohtml.detect.detect`; it replaces ``chardet.detect`` and ``charset_normalizer.from_bytes``:

.. testcode::

    from turbohtml.detect import detect

    raw = "Précédemment, la créativité française".encode("cp1252")
    match = detect(raw)
    print(match.encoding, match.language)
    print(raw.decode(match.codec))

.. testoutput::

    windows-1252 None
    Précédemment, la créativité française

Decode through ``match.codec``, not ``match.encoding``. The two are different strings: ``encoding`` is the WHATWG name,
and the CPython codec that answers to the same name is a different encoding -- ``bytes.decode("big5")`` reaches a strict
subset of the spec's Big5, ``koi8-u`` reaches KOI8-U where the spec means KOI8-RU, and ``x-mac-cyrillic`` reaches no
codec at all. ``match.codec`` names a ``whatwg-*`` codec :mod:`turbohtml.detect` registers, whose decoder is the one
:func:`turbohtml.parse` uses, so the text you get back is the text the parser would have seen.

Rank the alternatives with :func:`~turbohtml.detect.detect_all`, constrain or threshold them with a
:class:`~turbohtml.detect.Detection` config, and feed a stream chunk by chunk with a
:class:`~turbohtml.detect.EncodingDetector`, which scores each chunk as it arrives and holds a fixed amount of memory
whatever the stream's length. Where the chunks fall never changes the answer:

.. testcode::

    from io import BytesIO

    from turbohtml.detect import EncodingDetector

    stream = BytesIO("\ufeffstreamed UTF-8 content".encode())
    detector = EncodingDetector()
    for chunk in iter(lambda: stream.read(4096), b""):
        detector.feed(chunk)
        if detector.done:  # the byte-order mark already decided the stream
            break
    print(detector.close().encoding)

.. testoutput::

    UTF-8-SIG

Normalize decoded text to a Unicode normalization form
======================================================

Once bytes are decoded, the same visible text can carry different code points -- ``"é"`` as one character or as an ``e``
plus a combining accent -- so equality, search, and deduplication need a normalization pass first.
:func:`turbohtml.detect.normalize` runs all four UAX #15 forms in C, the successor to
:func:`python:unicodedata.normalize`:

.. testcode::

    from turbohtml.detect import is_normalized, normalize

    composed = "café"
    decomposed = "café"
    print(composed == decomposed)
    print(normalize("NFC", decomposed) == composed)
    print(is_normalized("NFC", decomposed))
    print(normalize("NFKC", "ﬁle") == "file")

.. testoutput::

    False
    True
    False
    True

Use ``NFC`` to compare or store user text, ``NFKC`` to additionally flatten presentation variants (ligatures,
superscripts, width variants), and the ``NFD`` / ``NFKD`` forms when you want the fully decomposed representation.
:func:`~turbohtml.detect.is_normalized` answers the membership question without building the normalized copy.
