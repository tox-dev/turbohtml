#####################
 Reading messy bytes
#####################

The tutorials so far started from a ready ``str``. Real input arrives as bytes off a socket or a file, often with no
reliable label for its encoding and sometimes malformed. This tutorial decodes bytes the way a browser would, inspects
what the sniffer chose, and shows that broken markup still parses.

*****************
 Parse raw bytes
*****************

:func:`turbohtml.parse` accepts ``bytes`` and runs the WHATWG sniffing algorithm: a byte-order mark, then a ``<meta>``
declaration, defaulting to windows-1252. Read :attr:`~turbohtml.Document.encoding` for the name it resolved:

.. testcode::

    import turbohtml

    doc = turbohtml.parse(b'<meta charset="iso-8859-2"><p>\xe1</p>')
    print(doc.encoding)
    print(doc.find("p").text)

.. testoutput::

    ISO-8859-2
    á

The ``0xe1`` byte is ``á`` in ISO-8859-2, and the ``<meta>`` tag is what tells the parser so. Pass ``encoding=`` to
override the sniff when you know better than the document does.

*************************
 Inspect what was chosen
*************************

When there is no label at all, the parser falls back to the statistical detector. To see its verdict without building a
tree -- to decode a file yourself, or to log what you received -- call :func:`turbohtml.detect.detect`:

.. testcode::

    from turbohtml.detect import detect

    raw = "Précédemment, la créativité française".encode("cp1252")
    match = detect(raw)
    print(match.encoding, match.language)
    print(raw.decode(match.codec))

.. testoutput::

    windows-1252 None
    Précédemment, la créativité française

Decode through ``match.codec``, not ``match.encoding``: ``encoding`` is the WHATWG label, and the CPython codec of the
same name is a different encoding. ``codec`` names a ``whatwg-*`` codec :mod:`turbohtml.detect` registers, so the call
works straight off. The :doc:`/how-to/encoding` guide covers ranking alternatives and feeding a stream chunk by chunk.

*********************
 Recover from a mess
*********************

Bytes from the wild are often malformed. turbohtml builds the tree a browser builds, so an unclosed tag and a stray
attribute recover rather than raise. This snippet has no ``</td>``, no ``</tr>``, and a bare ``<`` in the text, and it
still yields the two cells:

.. testcode::

    broken = b"<table><tr><td>1 < 2<td>ok<tr><td>row two"
    cells = [td.text for td in turbohtml.parse(broken).find_all("td")]
    print(cells)

.. testoutput::

    ['1 < 2', 'ok', 'row two']

There is no strict mode to trip over: malformed input recovers the WHATWG way, the same way it does in a browser. With
bytes handled, :doc:`extracting-content` pulls the article and its data out of a full page.
