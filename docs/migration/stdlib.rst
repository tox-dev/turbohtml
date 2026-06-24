###########################
 From the standard library
###########################

Python's standard library ships HTML primitives in the :mod:`python:html` package: :func:`python:html.escape` and
:func:`python:html.unescape` for entity handling, and :class:`python:html.parser.HTMLParser`, a SAX-style,
non-WHATWG-conformant tokenizer you subclass with ``handle_*`` callbacks.

***************
 Why turbohtml
***************

:func:`turbohtml.escape` and :func:`turbohtml.unescape` reproduce the standard-library functions byte for byte, so they
are drop-ins, but scan with SIMD and run several times faster. The tokenizer and :func:`turbohtml.parse` are
WHATWG-conformant where ``html.parser`` is not, and the whole surface is fully type annotated:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - operation
      - turbohtml
      - standard library
      - speed-up
    - - escape medium markup (4 KiB)
      - 2.27 µs
      - 7.19 µs
      - 3.2x
    - - unescape medium dense refs (4 KiB)
      - 8.10 µs
      - 69.3 µs
      - 8.6x
    - - tokenize typical markup
      - 34.9 µs
      - 435 µs
      - 12.5x
    - - feed and dispatch wpt page (9.6 kB)
      - 82.1 µs
      - 362 µs
      - 4.4x

*********************
 Escape and unescape
*********************

.. testcode::

    import html
    from turbohtml import escape, unescape

    print(escape('<a href="x">') == html.escape('<a href="x">'))
    print(unescape("caf&eacute; &#127881;") == html.unescape("caf&eacute; &#127881;"))

.. testoutput::

    True
    True

*********************
 html.parser adapter
*********************

To keep an existing :class:`python:html.parser.HTMLParser` subclass, swap its base class for
:class:`turbohtml.migration.stdlib.HTMLParser`: the same ``handle_*`` callbacks and ``feed``/``close`` methods run over
the WHATWG-conformant tokenizer. Or drop the subclass and take the token stream from :func:`turbohtml.tokenize` (or
:meth:`turbohtml.Tokenizer.feed` for incremental input), or skip tokens entirely and :func:`turbohtml.parse` straight to
a tree. All three are WHATWG-conformant, unlike ``html.parser``. The :doc:`/how-to/index` guide has a worked port.

``HTMLParser`` is a SAX-style callback API; turbohtml gives you the events as a token stream you drive yourself, which
inverts the control flow. Each ``handle_*`` override becomes a branch on :attr:`Token.type <turbohtml.Token.type>`:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - ``html.parser`` callback
      - turbohtml token
    - - ``handle_starttag(tag, attrs)``
      - ``token.type is TokenType.START_TAG`` → ``token.tag``, ``token.attrs``
    - - ``handle_startendtag(tag, attrs)``
      - ``TokenType.START_TAG`` with ``token.self_closing``
    - - ``handle_endtag(tag)``
      - ``TokenType.END_TAG`` → ``token.tag``
    - - ``handle_data(data)``
      - ``TokenType.TEXT`` → ``token.data``
    - - ``handle_comment(data)``
      - ``TokenType.COMMENT`` → ``token.data``
    - - ``handle_decl(decl)``
      - ``TokenType.DOCTYPE`` → ``token.name``
    - - ``handle_entityref``/``handle_charref``
      - ``tokenize(..., resolve_references=False)`` → ``TokenType.CHARACTER_REFERENCE``, else resolved in ``token.data``
    - - ``get_starttag_text()``
      - ``tokenize(..., capture_source=True)`` → ``token.source``

.. testcode::

    import turbohtml
    from turbohtml import TokenType

    events = []
    for token in turbohtml.tokenize('<p class="x">Hi &amp; bye</p>'):
        if token.type is TokenType.START_TAG:
            events.append(("start", token.tag, token.attrs))
        elif token.type is TokenType.TEXT:
            events.append(("data", token.data))
        elif token.type is TokenType.END_TAG:
            events.append(("end", token.tag))
    print(events)

.. testoutput::

    [('start', 'p', [('class', 'x')]), ('data', 'Hi & bye'), ('end', 'p')]

**********
 Pitfalls
**********

- The token stream inverts ``html.parser``'s callback control flow: you loop over tokens and branch on :attr:`Token.type
  <turbohtml.Token.type>` instead of overriding ``handle_*`` (unless you subclass
  :class:`turbohtml.migration.stdlib.HTMLParser`, which keeps the callbacks).
- By default ``token.data`` already holds decoded text (the equivalent of ``convert_charrefs=True``). To recover the
  split stream ``convert_charrefs=False`` gives, pass ``resolve_references=False`` and handle
  ``TokenType.CHARACTER_REFERENCE`` tokens, whose ``token.source`` is the verbatim reference and ``token.data`` its
  resolved value. The verbatim start-tag text ``get_starttag_text()`` returns is ``token.source`` once you pass
  ``capture_source=True``.
