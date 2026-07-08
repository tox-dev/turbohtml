###########################
 From the standard library
###########################

Python's standard library ships HTML primitives in the :mod:`python:html` package. :func:`python:html.escape` and
:func:`python:html.unescape` handle entity encoding and decoding, :mod:`python:html.entities` exposes the reference
tables, and :class:`python:html.parser.HTMLParser` is a SAX-style tokenizer you subclass and drive with ``handle_*``
callbacks. These are the zero-dependency, always-available building blocks that ship with CPython; many scripts,
templating helpers, and scrapers reach for them because they are already installed. The scope stops at tokenizing and
entity work: ``html.parser`` does not build a document tree, does not implement WHATWG error recovery, and is explicitly
documented as not fully HTML5-conformant.

turbohtml covers that same ground and extends past it. :func:`turbohtml.escape` and :func:`turbohtml.unescape` match the
stdlib functions byte for byte, :func:`turbohtml.tokenize` and :class:`turbohtml.Tokenizer` replace the callback
tokenizer, and :class:`turbohtml.migration.stdlib.HTMLParser` keeps your existing ``handle_*`` subclass working
unchanged. Everything runs over a WHATWG-conformant C core that also builds a full parse tree, which ``html.parser`` has
no equivalent for. The same C core also covers :mod:`python:unicodedata`'s Unicode normalization, so
:func:`turbohtml.detect.normalize` is a drop-in for :func:`python:unicodedata.normalize`.

*********************
 turbohtml vs stdlib
*********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - stdlib (``html``)
    - - Scope
      - Escape/unescape, tokenizer, and full WHATWG tree construction
      - Escape/unescape, entity tables, and a tokenizer only (no tree)
    - - Feature breadth
      - Tokens, tree, selectors, serialization, plus the callback shim
      - ``escape``/``unescape``, ``html.entities`` tables, ``HTMLParser`` callbacks
    - - Performance
      - SIMD scanning, several times faster on escape/unescape
      - Pure-Python entity scan and tokenizer
    - - Typing
      - Fully type annotated across the public surface
      - Annotated in typeshed stubs, not conformant behavior
    - - Dependencies
      - Compiled C extension (wheels), installed from PyPI
      - Built into CPython, zero install
    - - Maintenance
      - Actively developed, tracks the WHATWG spec
      - Stable CPython module, ``html.parser`` frozen as non-conformant

Feature overlap
===============

The shared surface ports one-to-one:

- :func:`python:html.escape` → :func:`turbohtml.escape`, same signature and output.
- :func:`python:html.unescape` → :func:`turbohtml.unescape`, same signature and output.
- :class:`python:html.parser.HTMLParser` subclasses → :class:`turbohtml.migration.stdlib.HTMLParser`, same ``handle_*``
  callbacks and ``feed``/``close``/``reset``/``getpos`` methods.

What turbohtml adds
===================

- WHATWG-conformant tokenizing and tree construction via :func:`turbohtml.parse` and :func:`turbohtml.parse_fragment`;
  ``html.parser`` tokenizes but never builds a tree and is documented as not HTML5-conformant.
- A token stream you drive yourself through :func:`turbohtml.tokenize` and :class:`turbohtml.Tokenizer`, instead of
  inverting control into callbacks.
- An event-driven parse, :func:`turbohtml.saxparse.sax_parse`, that keeps ``html.parser``'s callback shape but fires on
  the constructed tree -- implied tags, foster parenting, the adoption agency -- which the standard library never
  builds.
- Verbatim source capture per token (``capture_source=True`` → ``token.source``) and unresolved reference tokens
  (``resolve_references=False`` → ``TokenType.CHARACTER_REFERENCE``).
- SIMD-accelerated escape/unescape scanning.

What stdlib has that turbohtml does not
=======================================

- ``html.parser`` and the ``html`` functions are built into CPython with no install step. turbohtml ships a compiled
  extension from PyPI; in environments that cannot install wheels or build C, the stdlib remains the only option.
- :mod:`python:html.entities` exposes the raw reference tables (``name2codepoint``, ``codepoint2name``, ``html5``) as
  public data. turbohtml resolves references through ``escape``/``unescape`` and the tokenizer rather than exposing the
  dicts; if you consume those tables directly, keep importing ``html.entities``.

Performance
===========

.. bench-table::
    :file: bench/stdlib.json

:func:`turbohtml.escape` and :func:`turbohtml.unescape` reproduce the standard-library functions byte for byte, so they
are drop-ins, but scan with SIMD and run several times faster.

****************
 How to migrate
****************

Swap the imports and, if you subclass the parser, swap the base class:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - stdlib call
      - turbohtml call
    - - ``html.escape(s)``
      - ``turbohtml.escape(s)``
    - - ``html.unescape(s)``
      - ``turbohtml.unescape(s)``
    - - ``class P(html.parser.HTMLParser)``
      - ``class P(turbohtml.migration.stdlib.HTMLParser)``
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

Escape and unescape are literal drop-ins:

.. testcode::

    import html
    from turbohtml import escape, unescape

    print(escape('<a href="x">') == html.escape('<a href="x">'))
    print(unescape("caf&eacute; &#127881;") == html.unescape("caf&eacute; &#127881;"))

.. testoutput::

    True
    True

To keep an existing :class:`python:html.parser.HTMLParser` subclass, swap its base class for
:class:`turbohtml.migration.stdlib.HTMLParser`: the same ``handle_*`` callbacks and ``feed``/``close`` methods run over
the WHATWG-conformant tokenizer. Or drop the subclass and take the token stream from :func:`turbohtml.tokenize` (or
:meth:`turbohtml.Tokenizer.feed` for incremental input), or skip tokens entirely and :func:`turbohtml.parse` straight to
a tree. All three are WHATWG-conformant, unlike ``html.parser``. The :doc:`/how-to/index` guide has a worked port.

``HTMLParser`` is a SAX-style callback API; turbohtml gives you the events as a token stream you drive yourself, which
inverts the control flow. Each ``handle_*`` override becomes a branch on :attr:`Token.type <turbohtml.Token.type>`:

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

If you liked ``html.parser``'s callback shape and only want the WHATWG-correct tree behind it,
:func:`turbohtml.saxparse.sax_parse` keeps that shape: subclass :class:`turbohtml.saxparse.SaxHandler`, override the
events you need, and the parser fires them on the constructed tree rather than the raw tags. The ``handle_*`` methods
map onto SAX methods one-to-one:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - ``html.parser`` override
      - ``SaxHandler`` override
    - - ``handle_starttag(tag, attrs)``
      - ``start_element(tag, attrs)``
    - - ``handle_endtag(tag)``
      - ``end_element(tag)``
    - - ``handle_data(data)``
      - ``characters(data)``
    - - ``handle_comment(data)``
      - ``comment(data)``
    - - ``handle_decl(decl)``
      - ``doctype(name, public_id, system_id)``
    - - ``handle_pi(data)``
      - ``processing_instruction(data)``

.. testcode::

    from turbohtml.saxparse import SaxHandler, sax_parse


    class Collector(SaxHandler):
        def __init__(self):
            self.starts = []

        def start_element(self, tag, attrs):
            self.starts.append(tag if not attrs else f"{tag} {dict(attrs)}")


    collector = Collector()
    sax_parse("<table><td>cell", collector)
    print(collector.starts)

.. testoutput::

    ['html', 'head', 'body', 'table', 'tbody', 'tr', 'td']

``html.parser`` would report just ``table`` and ``td``; the SAX events carry the implied ``html``/``head``/``body`` and
the foster-parented ``tbody``/``tr`` the tree builder inserts. Unlike ``html.parser``, ``sax_parse`` builds the working
tree (freed at the end) rather than streaming in constant space, so it is the tool for a spec-correct one-pass
extraction, not for a document larger than memory. The :doc:`/how-to/sax` guide has more, and :doc:`/explanation/sax`
covers the memory model.

***********************
 Unicode normalization
***********************

:func:`python:unicodedata.normalize` and :func:`python:unicodedata.is_normalized` move to
:func:`turbohtml.detect.normalize` and :func:`turbohtml.detect.is_normalized`: the form name comes first and the output
is identical, because turbohtml runs the four forms in C over tables generated from the interpreter's own
``unicodedata``. A quick check returns already-normalized text untouched.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - stdlib call
      - turbohtml call
    - - ``unicodedata.normalize("NFC", s)``
      - ``turbohtml.detect.normalize("NFC", s)``
    - - ``unicodedata.is_normalized("NFC", s)``
      - ``turbohtml.detect.is_normalized("NFC", s)``

.. testcode::

    import unicodedata

    from turbohtml.detect import normalize

    forms = ("NFC", "NFD", "NFKC", "NFKD")
    text = "ﬁ café ẛ̣"
    print(all(normalize(form, text) == unicodedata.normalize(form, text) for form in forms))

.. testoutput::

    True

**********************
 Gotchas and pitfalls
**********************

- The token stream inverts ``html.parser``'s callback control flow: you loop over tokens and branch on :attr:`Token.type
  <turbohtml.Token.type>` instead of overriding ``handle_*`` (unless you subclass
  :class:`turbohtml.migration.stdlib.HTMLParser`, which keeps the callbacks).
- By default ``token.data`` already holds decoded text (the equivalent of ``convert_charrefs=True``). To recover the
  split stream ``convert_charrefs=False`` gives, pass ``resolve_references=False`` and handle
  ``TokenType.CHARACTER_REFERENCE`` tokens, whose ``token.source`` is the verbatim reference and ``token.data`` its
  resolved value. On :class:`turbohtml.migration.stdlib.HTMLParser` the ``convert_charrefs`` argument is accepted for
  signature compatibility but ignored; references are always resolved.
- The verbatim start-tag text ``get_starttag_text()`` returns is ``token.source`` once you pass ``capture_source=True``;
  it is not captured by default.
- ``html.parser`` is documented as not fully HTML5-conformant, so tricky recovery cases (malformed tags, misnested
  elements, foreign content) can tokenize differently. turbohtml follows the WHATWG spec, so output may diverge from a
  legacy ``html.parser`` run on the same broken input; the turbohtml result is the conformant one.
- If your code imports the reference tables from :mod:`python:html.entities` directly, keep that import: turbohtml does
  not re-export ``name2codepoint``/``codepoint2name``/``html5``.
