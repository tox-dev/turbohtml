###############
 Tokenize HTML
###############

**************************
 Migrate from html.parser
**************************

The quickest port keeps your subclass: :class:`turbohtml.migration.stdlib.HTMLParser` is a drop-in base class with the
same ``handle_*`` callbacks and ``feed``/``close`` methods, over the WHATWG-conformant tokenizer. Change the import and
the base class and the handlers fire as before:

.. testcode::

    from turbohtml.migration.stdlib import HTMLParser

    class LinkCollector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.links += [v for n, v in attrs if n == "href" and v]

    collector = LinkCollector()
    collector.feed('<a href="/x">x</a> <a href="/y">y</a>')
    collector.close()
    print(collector.links)

.. testoutput::

    ['/x', '/y']

It differs from ``html.parser`` only where ``html.parser`` diverges from the WHATWG algorithm: references are always
resolved (so ``handle_entityref``/``handle_charref`` never fire), and a processing instruction or CDATA section reaches
``handle_comment`` rather than ``handle_pi``/``unknown_decl``, because the HTML spec treats both as comments.

If you would rather drop the subclass entirely, turbohtml also exposes the raw token stream.
:class:`python:html.parser.HTMLParser` is callback-driven: you subclass it and override a handler per event. turbohtml
inverts that into a token stream you iterate, which removes the subclass, the mutable handler state, and the
per-callback Python call overhead. A typical parser:

.. code-block:: python

    from html.parser import HTMLParser


    class LinkCollector(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.links: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "a":
                self.links.extend(
                    value for name, value in attrs if name == "href" and value
                )


    collector = LinkCollector()
    collector.feed(page)
    collector.close()

becomes a loop:

.. code-block:: python

    import turbohtml

    links = [
        href
        for token in turbohtml.tokenize(page)
        if token.type is turbohtml.TokenType.START_TAG
        and token.tag == "a"
        and (href := token.attr("href"))
    ]

The events map one to one:

- ``handle_starttag(tag, attrs)`` → a token with ``type is TokenType.START_TAG``; ``token.tag`` and ``token.attrs``
  carry the same lowercased name and decoded ``(name, value)`` pairs, and ``token.attr(name)`` replaces scanning the
  list.
- ``handle_endtag(tag)`` → ``TokenType.END_TAG``.
- ``handle_startendtag(tag, attrs)`` → a ``START_TAG`` token with ``self_closing`` true (turbohtml does not emit a
  separate event).
- ``handle_data(data)`` → ``TokenType.TEXT``; character references arrive decoded, like ``convert_charrefs=True``, so
  there is no ``handle_entityref``/``handle_charref`` pair to implement.
- ``handle_comment(data)`` → ``TokenType.COMMENT``.
- ``handle_decl(decl)`` → ``TokenType.DOCTYPE``, split into ``name``, ``public_id`` and ``system_id`` instead of one raw
  string.
- ``self.getpos()`` → ``token.line`` and ``token.col``, the same 1-based-line, 0-based-column convention.
- ``feed()``/``close()`` → the same names on :class:`turbohtml.Tokenizer`; each ``feed()`` returns the tokens that chunk
  completed instead of firing callbacks, and a ``with`` block replaces remembering ``close()``.

turbohtml differs from ``html.parser`` wherever ``html.parser`` diverges from the WHATWG algorithm browsers implement:
turbohtml handles the raw-text content models (a ``<b>`` inside ``<script>`` stays text rather than a tag), recovers
from malformed markup the way a browser would, and never emits ``handle_decl`` for CDATA sections (they only exist in
foreign content). Code ported from ``html.parser`` sees the same tokens a browser sees, the point of most migrations.

*****************************
 Extract the links of a page
*****************************

Iterate the token stream and pull the ``href`` of every anchor start tag; :meth:`turbohtml.Token.attr` returns the empty
string for a valueless attribute and your fallback when the attribute is missing:

.. testcode::

    page = '<p><a href="/a">one</a> and <a href="/b" download>two</a></p>'
    print([token.attr("href") for token in turbohtml.tokenize(page)
     if token.type is turbohtml.TokenType.START_TAG and token.tag == "a"])

.. testoutput::

    ['/a', '/b']

**********************************
 Extract the visible text of HTML
**********************************

Collect the text tokens while skipping the contents of elements the browser does not render, such as ``script`` and
``style``. The tokenizer hands you script and style bodies as text tokens (that is what they are to the algorithm), so
track the enclosing tag yourself:

.. testcode::

    from collections.abc import Iterator
    def visible_text(page: str) -> Iterator[str]:
        hidden = 0
        for token in turbohtml.tokenize(page):
            if token.type is turbohtml.TokenType.START_TAG and token.tag in {"script", "style"}:
                hidden += 1
            elif token.type is turbohtml.TokenType.END_TAG and token.tag in {"script", "style"}:
                hidden -= 1
            elif token.type is turbohtml.TokenType.TEXT and not hidden:
                yield token.data
    print("".join(visible_text("<style>p{}</style><p>Tom &amp; Jerry</p>")))

.. testoutput::

    Tom & Jerry

***********************************
 Tokenize a document incrementally
***********************************

When the input arrives in chunks, feed each chunk to a :class:`turbohtml.Tokenizer` and consume the tokens it returns;
text and unfinished tags stay buffered until they are complete, so the result is identical to tokenizing the whole
string at once:

.. testcode::

    tokenizer = turbohtml.Tokenizer()
    tokens = []
    for chunk in ("<ul><li>on", "e<li>two</", "ul>"):
        tokens += tokenizer.feed(chunk)
    tokens += tokenizer.close()
    print([token.tag or token.data for token in tokens])

.. testoutput::

    ['ul', 'li', 'one', 'li', 'two', 'ul']

As a context manager the tokenizer signals end of input when the block exits, so forgetting ``close()`` cannot leave the
final tokens stuck behind an unfinished construct; iterate the tokenizer itself to drain what remains:

.. testcode::

    with turbohtml.Tokenizer() as tokenizer:
        tokens = [token for chunk in ("<ul><li>on", "e") for token in tokenizer.feed(chunk)]
    print([token.tag or token.data for token in tokenizer])

.. testoutput::

    ['one']

Call ``reset()`` to reuse the same tokenizer for an unrelated document.

****************************************
 Report source positions in diagnostics
****************************************

Every token remembers where it began: :attr:`turbohtml.Token.line` is the 1-based source line and
:attr:`turbohtml.Token.col` the 0-based column (the convention :mod:`python:html.parser` shares), so you can point at
the offending markup:

.. testcode::

    page = "<h1>title</h1>\n<img src='a.png'>"
    print([f"{token.tag} at {token.line}:{token.col}" for token in turbohtml.tokenize(page)
     if token.type is turbohtml.TokenType.START_TAG and token.tag == "img"])

.. testoutput::

    ['img at 2:0']

*****************************************
 See each character reference separately
*****************************************

By default a character reference is decoded into the surrounding text, so ``Tom &amp; Jerry`` is one text token reading
``Tom & Jerry``. Pass ``resolve_references=False`` to :func:`turbohtml.tokenize` (or :class:`turbohtml.Tokenizer`) to
receive each reference in text as its own :attr:`~turbohtml.TokenType.CHARACTER_REFERENCE` token instead:
:attr:`~turbohtml.Token.data` is the resolved value and :attr:`~turbohtml.Token.source` the verbatim ``&...;`` (so
``source[1] == "#"`` tells a numeric reference from a named one). A bare ``&`` that is not a reference stays text, and
attribute values are always decoded:

.. testcode::

    import turbohtml
    from turbohtml import TokenType

    tokens = turbohtml.tokenize("5 &lt; 10 &amp; rising", resolve_references=False)
    print([(token.data, token.source) for token in tokens if token.type is TokenType.CHARACTER_REFERENCE])

.. testoutput::

    [('<', '&lt;'), ('&', '&amp;')]

***********************************
 Keep the verbatim source of a tag
***********************************

The tokenizer normalizes tags: names lowercase, attribute order and quoting collapse. When you need the exact bytes a
token came from - to rewrite markup in place, or to report it untouched - pass ``capture_source=True`` and read
:attr:`turbohtml.Token.source`, the verbatim slice of the input. It is set for start tags, end tags, comments, and
DOCTYPEs (text tokens leave it ``None``):

.. testcode::

    tag = next(iter(turbohtml.tokenize("<IMG  SRC='a.png'>", capture_source=True)))
    print(tag.tag, "from", repr(tag.source))

.. testoutput::

    img from "<IMG  SRC='a.png'>"
