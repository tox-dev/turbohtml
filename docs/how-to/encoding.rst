############################
 Handle character encodings
############################

Decode bytes of unknown or declared encoding the way a browser would, and inspect what the sniffer chose, with
:func:`turbohtml.parse` and :func:`turbohtml.detect.detect`.

************************************
 Parse bytes of an unknown encoding
************************************

:func:`turbohtml.parse` accepts ``bytes`` and runs the WHATWG encoding sniffing algorithm: a byte-order mark, then the
``encoding`` argument, then a ``<meta>`` declaration, then a structural UTF-8 check, defaulting to windows-1252. Pass
``encoding`` to outrank the ``<meta>`` and the sniff -- a byte-order mark still outranks it -- and read
:attr:`~turbohtml.Document.encoding` for the WHATWG name that was resolved:

.. testcode::

    import turbohtml

    doc = turbohtml.parse(b'<meta charset="iso-8859-2"><p>\xe1</p>')
    print(doc.encoding)
    print(doc.find("p").text)

.. testoutput::

    ISO-8859-2
    á

*********************************
 Tell a declaration from a guess
*********************************

:attr:`~turbohtml.Document.encoding_confidence` reports which of the sniff's steps answered. It is ``"certain"`` when
the document named its own encoding, through a byte-order mark, the ``encoding`` argument, or a ``<meta>`` charset. It
is ``"tentative"`` when nothing did and the sniff fell back on a structural UTF-8 read, the opt-in detector, or
windows-1252. Chasing a mojibake report, that tells a page that declared the wrong encoding from one that declared none:

.. testcode::

    import turbohtml

    print(turbohtml.parse(b'<meta charset="utf-8"><p>caf\xc3\xa9</p>').encoding_confidence)
    print(turbohtml.parse(b"<p>caf\xc3\xa9</p>").encoding_confidence)
    print(turbohtml.parse("<p>café</p>").encoding_confidence)

.. testoutput::

    certain
    tentative
    None

A ``<meta>`` charset counts wherever it sits, even past the 1024 bytes the prescan reads: :func:`turbohtml.parse` redoes
the parse against a declaration it could not reach in time, which is the WHATWG "changing the encoding while parsing"
step. :func:`turbohtml.detect.detect` has no tree to consult, so it stops at the prescan and can disagree with
:func:`turbohtml.parse` on such a document; html5lib draws the same line between its input stream's encoding and its
parser's ``documentEncoding``.

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

*********************************************
 Tell the detector where the bytes came from
*********************************************

Frequency scoring has little to work with on short text, where several encodings of one script score alike. The host
that served the page is evidence a browser already uses. Pass the hostname's rightmost DNS label as ``tld`` and the
detector weighs the candidates the way Firefox does: the encoding that domain expects gains a point, the ones native to
it keep their score, and the rest pay a penalty.

.. testcode::

    from turbohtml.detect import Detection, detect

    raw = "Příliš žluťoučký kůň úpěl ďábelské ódy".encode("iso-8859-2")
    print(detect(raw).encoding)
    print(detect(raw, Detection(tld="cz")).encoding)
    print(detect(raw, Detection(tld="ru")).encoding)

.. testoutput::

    ISO-8859-2
    ISO-8859-2
    windows-1252

Give the label alone, lower-case, with no leading dot: ``"cz"``, not ``"example.cz"``. Use the Punycode form for an
internationalized domain, so ``"xn--p1ai"`` and not ``"рф"``. Anything else raises :exc:`ValueError`, since a misspelled
label would otherwise read as no hint at all. A generic label such as ``"com"`` says nothing about script and changes
nothing, and neither does the default ``None``.

A two-letter label the classifier does not carry reads as Western European, which is still a hint. Above, ``.ru`` rules
out the Central European candidates and leaves windows-1252 standing. A ``.th`` label would rule out nothing, because
the bytes hold no Thai for its expectation to rest on.

The hint reaches frequency scoring and stops there. A byte-order mark, a ``<meta>`` charset, and structurally valid
UTF-8 are proofs rather than guesses, and no domain outvotes them.

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
