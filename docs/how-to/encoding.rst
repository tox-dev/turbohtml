############################
 Handle character encodings
############################

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

*******************************************
 Detect an encoding without parsing at all
*******************************************

When you only need the encoding -- to hand the bytes to another decoder, or to log what you received --
:mod:`turbohtml.detect` exposes the same engine as a standalone ``bytes -> encoding`` guess, a drop-in for ``chardet``
or ``charset-normalizer``. :func:`~turbohtml.detect.detect` returns the single best
:class:`~turbohtml.detect.EncodingMatch`:

.. testcode::

    from turbohtml.detect import detect

    match = detect("Привет, как дела сегодня?".encode("windows-1251"))
    print(match.encoding, match.language)

.. testoutput::

    windows-1251 Russian

Use :func:`~turbohtml.detect.detect_all` for the ranked alternatives, and pass a :class:`~turbohtml.detect.Detection`
config to set a confidence floor or restrict the candidate encodings. For a stream that arrives in chunks, feed a
:class:`~turbohtml.detect.Detector` and resolve on close:

.. testcode::

    from turbohtml.detect import Detector

    detector = Detector()
    detector.feed("こんにちは".encode("shift_jis"))
    detector.feed("世界".encode("shift_jis"))
    print(detector.close().encoding)

.. testoutput::

    Shift_JIS
