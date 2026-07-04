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
    print(raw.decode(match.encoding))

.. testoutput::

    windows-1252 None
    Précédemment, la créativité française

Every name :func:`~turbohtml.detect.detect` can return is a valid :mod:`python:codecs` alias, so the decode call works
directly. Rank the alternatives with :func:`~turbohtml.detect.detect_all`, constrain or threshold them with a
:class:`~turbohtml.detect.Detection` config, and feed a stream chunk by chunk with a
:class:`~turbohtml.detect.EncodingDetector`:

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
