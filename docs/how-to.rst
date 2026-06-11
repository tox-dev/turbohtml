###############
 How-to guides
###############

***************************************
 Escape untrusted text for HTML output
***************************************

When you interpolate user-supplied text into HTML, escape it first so it cannot break out of its context:

.. code-block:: pycon

    >>> import turbohtml
    >>> comment = '<script>alert("xss")</script>'
    >>> f"<p>{turbohtml.escape(comment)}</p>"
    '<p>&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;</p>'

************************************************
 Escape for a text node without touching quotes
************************************************

Inside element text (not an attribute) the quote characters are safe, so pass ``quote=False`` to leave them untouched
and keep the output smaller:

.. code-block:: pycon

    >>> turbohtml.escape('He said "hi" & left', quote=False)
    'He said "hi" &amp; left'

**********************************
 Decode HTML character references
**********************************

Convert named and numeric references from scraped or stored HTML back into text:

.. code-block:: pycon

    >>> turbohtml.unescape("&pound;10 &mdash; &#127881;")
    '£10 — 🎉'

Unescaping follows the HTML5 rules, including longest-match for references that omit the trailing semicolon:

.. code-block:: pycon

    >>> turbohtml.unescape("&notit;")
    '¬it;'

**************************
 Migrate from html.parser
**************************

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
- ``handle_data(data)`` → ``TokenType.TEXT``; character references arrive already decoded, like
  ``convert_charrefs=True``, so there is no ``handle_entityref``/``handle_charref`` pair to implement.
- ``handle_comment(data)`` → ``TokenType.COMMENT``.
- ``handle_decl(decl)`` → ``TokenType.DOCTYPE``, already split into ``name``, ``public_id`` and ``system_id`` instead of
  one raw string.
- ``self.getpos()`` → ``token.line`` and ``token.col``, the same 1-based-line, 0-based-column convention.
- ``feed()``/``close()`` → the same names on :class:`turbohtml.Tokenizer`; each ``feed()`` returns the tokens that chunk
  completed instead of firing callbacks, and a ``with`` block replaces remembering ``close()``.

Behavior differs where ``html.parser`` diverges from the WHATWG algorithm browsers implement: turbohtml handles the
raw-text content models exactly (a ``<b>`` inside ``<script>`` is text, not a tag), recovers from malformed markup the
way a browser would, and never emits ``handle_decl`` for CDATA sections (they only exist in foreign content). Code
ported from ``html.parser`` therefore sees the same tokens a browser sees, which is usually the migration's point.

*****************************
 Extract the links of a page
*****************************

Iterate the token stream and pull the ``href`` of every anchor start tag; :meth:`turbohtml.Token.attr` returns ``None``
for a valueless attribute and your fallback when the attribute is missing:

.. code-block:: pycon

    >>> page = '<p><a href="/a">one</a> and <a href="/b" download>two</a></p>'
    >>> [token.attr("href") for token in turbohtml.tokenize(page)
    ...  if token.type is turbohtml.TokenType.START_TAG and token.tag == "a"]
    ['/a', '/b']

**********************************
 Extract the visible text of HTML
**********************************

Collect the text tokens while skipping the contents of elements whose text is not rendered, such as ``script`` and
``style``. The tokenizer hands you script and style bodies as text tokens (that is what they are to the algorithm), so
track the enclosing tag yourself:

.. code-block:: pycon

    >>> from collections.abc import Iterator
    >>> def visible_text(page: str) -> Iterator[str]:
    ...     hidden = 0
    ...     for token in turbohtml.tokenize(page):
    ...         if token.type is turbohtml.TokenType.START_TAG and token.tag in {"script", "style"}:
    ...             hidden += 1
    ...         elif token.type is turbohtml.TokenType.END_TAG and token.tag in {"script", "style"}:
    ...             hidden -= 1
    ...         elif token.type is turbohtml.TokenType.TEXT and not hidden:
    ...             yield token.data
    ...
    >>> "".join(visible_text("<style>p{}</style><p>Tom &amp; Jerry</p>"))
    'Tom & Jerry'

***********************************
 Tokenize a document incrementally
***********************************

When the input arrives in chunks, feed each chunk to a :class:`turbohtml.Tokenizer` and consume the tokens it returns;
text and unfinished tags stay buffered until they are complete, so the result is identical to tokenizing the whole
string at once:

.. code-block:: pycon

    >>> tokenizer = turbohtml.Tokenizer()
    >>> tokens = []
    >>> for chunk in ("<ul><li>on", "e<li>two</", "ul>"):
    ...     tokens += tokenizer.feed(chunk)
    >>> tokens += tokenizer.close()
    >>> [token.tag or token.data for token in tokens]
    ['ul', 'li', 'one', 'li', 'two', 'ul']

As a context manager the tokenizer signals end of input when the block exits, so forgetting ``close()`` cannot leave the
final tokens stuck behind an unfinished construct; iterate the tokenizer itself to drain what remains:

.. code-block:: pycon

    >>> with turbohtml.Tokenizer() as tokenizer:
    ...     tokens = [token for chunk in ("<ul><li>on", "e") for token in tokenizer.feed(chunk)]
    >>> [token.tag or token.data for token in tokenizer]
    ['one']

Call ``reset()`` to reuse the same tokenizer for an unrelated document.

****************************************
 Report source positions in diagnostics
****************************************

Every token remembers where it began: :attr:`turbohtml.Token.line` is the 1-based source line and
:attr:`turbohtml.Token.col` the 0-based column (the convention :mod:`python:html.parser` also uses), which makes it easy
to point at the offending markup:

.. code-block:: pycon

    >>> page = "<h1>title</h1>\n<img src='a.png'>"
    >>> [f"{token.tag} at {token.line}:{token.col}" for token in turbohtml.tokenize(page)
    ...  if token.type is turbohtml.TokenType.START_TAG and token.tag == "img"]
    ['img at 2:0']
