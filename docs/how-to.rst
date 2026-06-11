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

Every token remembers where it began. :meth:`turbohtml.Token.getpos` returns a 1-based line and 0-based column, the same
convention as :meth:`python:html.parser.HTMLParser.getpos`, which makes it easy to point at the offending markup:

.. code-block:: pycon

    >>> page = "<h1>title</h1>\n<img src='a.png'>"
    >>> [(token.tag, token.getpos()) for token in turbohtml.tokenize(page)
    ...  if token.type is turbohtml.TokenType.START_TAG and token.tag == "img"]
    [('img', (2, 0))]
