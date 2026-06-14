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

    >>> turbohtml.unescape("&pound;10 &copy; &#127881;")
    '£10 © 🎉'

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

Collect the text tokens while skipping the contents of elements the browser does not render, such as ``script`` and
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
:attr:`turbohtml.Token.col` the 0-based column (the convention :mod:`python:html.parser` shares), so you can point at
the offending markup:

.. code-block:: pycon

    >>> page = "<h1>title</h1>\n<img src='a.png'>"
    >>> [f"{token.tag} at {token.line}:{token.col}" for token in turbohtml.tokenize(page)
    ...  if token.type is turbohtml.TokenType.START_TAG and token.tag == "img"]
    ['img at 2:0']

************************************
 Find elements in a parsed document
************************************

Parse the document with :func:`turbohtml.parse`, then query it with :meth:`~turbohtml.Node.find` (first match) or
:meth:`~turbohtml.Node.find_all` (every match). A keyword argument constrains an attribute; both work from the document
or from any element, searching its descendants:

.. code-block:: pycon

    >>> import turbohtml
    >>> doc = turbohtml.parse("<form><input name=email><input name=token type=hidden></form>")
    >>> doc.find("input", type="hidden").attrs["name"]
    'token'
    >>> [field.attrs["name"] for field in doc.find_all("input")]
    ['email', 'token']

************************************
 Collect the links of a parsed page
************************************

Collect the ``href`` of every anchor by iterating :meth:`~turbohtml.Node.find_all`; a missing attribute does not appear
in :attr:`~turbohtml.Element.attrs`:

.. code-block:: pycon

    >>> page = '<p><a href="/a">one</a> and <a href="/b" download>two</a></p>'
    >>> [link.attrs["href"] for link in turbohtml.parse(page).find_all("a")]
    ['/a', '/b']

***********************************
 Read the text or markup of a node
***********************************

:attr:`~turbohtml.Node.text` is the concatenated character data of a node's subtree, with references decoded;
:attr:`~turbohtml.Node.html` re-serializes the subtree back to HTML (attributes quoted, specials escaped):

.. code-block:: pycon

    >>> article = turbohtml.parse("<article><h1>Title</h1><p>Tom &amp; Jerry</p></article>").find("article")
    >>> article.text
    'TitleTom & Jerry'
    >>> article.find("p").html
    '<p>Tom &amp; Jerry</p>'

``text`` gathers every text node in the subtree, including the contents of ``script`` and ``style`` elements when they
sit inside it; filter those out by walking :attr:`~turbohtml.Node.descendants` yourself when you need only rendered
text.

**************************************
 Match nodes with structural patterns
**************************************

The node types are a sealed hierarchy with :py:data:`~object.__match_args__` set, so a ``match`` statement dispatches on
node kind and unpacks the defining field (``tag`` for an :class:`~turbohtml.Element`, ``data`` for a
:class:`~turbohtml.Text` or :class:`~turbohtml.Comment`):

.. code-block:: pycon

    >>> def summarize(node: turbohtml.Node) -> str:
    ...     match node:
    ...         case turbohtml.Element(tag):
    ...             return f"<{tag}>"
    ...         case turbohtml.Text(data):
    ...             return repr(data)
    ...         case turbohtml.Comment(data):
    ...             return f"<!--{data}-->"
    ...         case _:
    ...             return "?"
    ...
    >>> [summarize(child) for child in turbohtml.parse("<p>hi<!--x--><b>bold</b></p>").find("p")]
    ["'hi'", '<!--x-->', '<b>']

************************
 Parse an HTML fragment
************************

To parse markup that belongs inside a specific element (a table row, an SVG subtree), use
:func:`turbohtml.parse_fragment` with the context tag. It returns that context :class:`~turbohtml.Element` with the
parsed nodes as its children, applying the same insertion rules the element would impose in a full document:

.. code-block:: pycon

    >>> row = turbohtml.parse_fragment("<td>a<td>b", "tr")
    >>> [cell.text for cell in row.find_all("td")]
    ['a', 'b']
    >>> row.html
    '<tr><td>a</td><td>b</td></tr>'
