###############
 How-to guides
###############

***************************************
 Escape untrusted text for HTML output
***************************************

When you interpolate user-supplied text into HTML, escape it first so it cannot break out of its context:

.. testcode::

    import turbohtml
    comment = '<script>alert("xss")</script>'
    print(f"<p>{turbohtml.escape(comment)}</p>")

.. testoutput::

    <p>&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;</p>

************************************************
 Escape for a text node without touching quotes
************************************************

Inside element text (not an attribute) the quote characters are safe, so pass ``quote=False`` to leave them untouched
and keep the output smaller:

.. testcode::

    print(turbohtml.escape('He said "hi" & left', quote=False))

.. testoutput::

    He said "hi" &amp; left

****************************************
 Build safe HTML strings for a template
****************************************

When you assemble HTML from a mix of trusted markup and untrusted values, use :mod:`turbohtml.markup`. Wrapping a value
in :class:`~turbohtml.markup.Markup` declares it safe; combining it with plain text escapes that text, so a forgotten
escape cannot inject markup. It is a drop-in for `markupsafe <https://markupsafe.palletsprojects.com>`_, so a `Jinja2
<https://jinja.palletsprojects.com>`_ project migrates by changing the import:

.. testcode::

    from turbohtml.markup import Markup, escape

    user = "<script>alert(1)</script>"
    row = Markup("<li>{}</li>").format(user)
    print(row)
    print(Markup(", ").join(["<b>", escape("a & b")]))

.. testoutput::

    <li>&lt;script&gt;alert(1)&lt;/script&gt;</li>
    &lt;b&gt;, a &amp; b

**********************************
 Decode HTML character references
**********************************

Convert named and numeric references from scraped or stored HTML back into text:

.. testcode::

    print(turbohtml.unescape("&pound;10 &copy; &#127881;"))

.. testoutput::

    £10 © 🎉

Unescaping follows the HTML5 rules, including longest-match for references that omit the trailing semicolon:

.. testcode::

    print(turbohtml.unescape("&notit;"))

.. testoutput::

    ¬it;

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

************************************
 Find elements in a parsed document
************************************

Parse the document with :func:`turbohtml.parse`, then query it with :meth:`~turbohtml.Node.find` (first match) or
:meth:`~turbohtml.Node.find_all` (every match). A keyword argument constrains an attribute; both work from the document
or from any element, searching its descendants:

.. testcode::

    import turbohtml
    doc = turbohtml.parse("<form><input name=email><input name=token type=hidden></form>")
    print(doc.find("input", type="hidden").attrs["name"])
    print([field.attrs["name"] for field in doc.find_all("input")])

.. testoutput::

    token
    ['email', 'token']

************************************
 Collect the links of a parsed page
************************************

Collect the ``href`` of every anchor by iterating :meth:`~turbohtml.Node.find_all`; a missing attribute does not appear
in :attr:`~turbohtml.Element.attrs`:

.. testcode::

    page = '<p><a href="/a">one</a> and <a href="/b" download>two</a></p>'
    print([link.attrs["href"] for link in turbohtml.parse(page).find_all("a")])

.. testoutput::

    ['/a', '/b']

***********************************
 Read the text or markup of a node
***********************************

:attr:`~turbohtml.Node.text` is the concatenated character data of a node's subtree, with references decoded;
:attr:`~turbohtml.Node.html` re-serializes the subtree back to HTML (attributes quoted, specials escaped):

.. testcode::

    article = turbohtml.parse("<article><h1>Title</h1><p>Tom &amp; Jerry</p></article>").find("article")
    print(article.text)
    print(article.find("p").html)

.. testoutput::

    TitleTom & Jerry
    <p>Tom &amp; Jerry</p>

``text`` gathers every text node in the subtree, including the contents of ``script`` and ``style`` elements when they
sit inside it; filter those out by walking :attr:`~turbohtml.Node.descendants` yourself when you need only rendered
text.

**************************************
 Match nodes with structural patterns
**************************************

The node types are a sealed hierarchy with :py:data:`~object.__match_args__` set, so a ``match`` statement dispatches on
node kind and unpacks the defining field (``tag`` for an :class:`~turbohtml.Element`, ``data`` for a
:class:`~turbohtml.Text` or :class:`~turbohtml.Comment`):

.. testcode::

    def summarize(node: turbohtml.Node) -> str:
        match node:
            case turbohtml.Element(tag):
                return f"<{tag}>"
            case turbohtml.Text(data):
                return repr(data)
            case turbohtml.Comment(data):
                return f"<!--{data}-->"
            case _:
                return "?"
    print([summarize(child) for child in turbohtml.parse("<p>hi<!--x--><b>bold</b></p>").find("p")])

.. testoutput::

    ["'hi'", '<!--x-->', '<b>']

***************************
 Query with a CSS selector
***************************

:meth:`~turbohtml.Node.select` returns every descendant matching a CSS selector in document order;
:meth:`~turbohtml.Node.select_one` returns the first or ``None``. The matcher covers type, ``#id``, ``.class``, and
attribute selectors with the ``=``, ``~=``, ``|=``, ``^=``, ``$=``, ``*=`` operators, joined by the descendant, child
(``>``), adjacent (``+``), and general-sibling (``~``) combinators, with comma groups:

.. testcode::

    import turbohtml
    doc = turbohtml.parse('<ul><li class=on>a<li><a href="/x">b</a></ul>')
    print([li.text for li in doc.select("li.on")])
    print(doc.select_one('a[href^="/"]').text)

.. testoutput::

    ['a']
    b

To test a node you already hold rather than search beneath it, use :meth:`~turbohtml.Node.matches` (does this node
match) or :meth:`~turbohtml.Node.closest` (the nearest matching self-or-ancestor):

.. testcode::

    link = turbohtml.parse('<nav><a href="/x">home</a></nav>').select_one("a")
    print(link.matches("nav a"))
    print(link.closest("nav").tag)

.. testoutput::

    True
    nav

********************************
 Filter by attribute or pattern
********************************

:meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all` take a filter that is a string, a compiled regex, a
callable, a ``bool`` (present or absent), or a list of those, applied to the tag or to an attribute. ``class_`` matches
a token in the class list, and ``axis`` aims the search at something other than descendants:

.. testcode::

    import re, turbohtml
    doc = turbohtml.parse('<a class="btn lg" href="/a">A</a><a href="mailto:x">B</a>')
    print([a.attrs["href"] for a in doc.find_all("a", href=re.compile(r"^/"))])
    print(doc.find("a", class_="lg").text)

.. testoutput::

    ['/a']
    A

************************
 Serialize with control
************************

:attr:`~turbohtml.Node.html` and :attr:`~turbohtml.Node.inner_html` are the default WHATWG-conformant forms (outer and
children-only). :meth:`~turbohtml.Node.serialize` adds control: ``formatter`` selects the escaping through
:class:`~turbohtml.Formatter`, and ``indent`` (an int or string) switches to a pretty form that adds whitespace and so
does not preserve meaning. :meth:`~turbohtml.Node.encode` is the same but returns bytes:

.. testcode::

    import turbohtml
    from turbohtml import Formatter
    card = turbohtml.parse("<div><p>café &amp; co</p></div>").select_one("div")
    print(card.inner_html)
    print(card.serialize(formatter=Formatter.NAMED_ENTITIES))
    print(card.encode("ascii", formatter=Formatter.NAMED_ENTITIES))

.. testoutput::

    <p>café &amp; co</p>
    <div><p>caf&eacute; &amp; co</p></div>
    b'<div><p>caf&eacute; &amp; co</p></div>'

************************************
 Parse bytes of an unknown encoding
************************************

:func:`turbohtml.parse` accepts ``bytes`` and runs the WHATWG encoding sniffing algorithm (a byte-order mark, then a
``<meta>`` declaration, defaulting to windows-1252). Pass ``encoding`` to override the sniff, and read
:attr:`~turbohtml.Document.encoding` for the label that was used:

.. testcode::

    import turbohtml
    doc = turbohtml.parse(b'<meta charset="iso-8859-2"><p>\xe1</p>')
    print(doc.encoding)
    print(doc.find("p").text)

.. testoutput::

    iso-8859-2
    á

************************
 Parse an HTML fragment
************************

To parse markup that belongs inside a specific element (a table row, an SVG subtree), use
:func:`turbohtml.parse_fragment` with the context tag. It returns that context :class:`~turbohtml.Element` with the
parsed nodes as its children, applying the same insertion rules the element would impose in a full document:

.. testcode::

    row = turbohtml.parse_fragment("<td>a<td>b", "tr")
    print([cell.text for cell in row.find_all("td")])
    print(row.html)

.. testoutput::

    ['a', 'b']
    <tr><td>a</td><td>b</td></tr>

**********************
 Build a tree by hand
**********************

Construct nodes with :class:`~turbohtml.Element`, :class:`~turbohtml.Text`, and :class:`~turbohtml.Comment`, then
assemble them. A list value for a token-list attribute (``class``, ``rel``, ...) joins on a space, and the ``text``
setter fills an element with a single text child:

.. testcode::

    from turbohtml import Element
    card = Element("article", {"class": ["card", "lg"]})
    heading = Element("h2")
    heading.text = "Title"
    card.append(heading)
    print(card.html)

.. testoutput::

    <article class="card lg"><h2>Title</h2></article>

*****************************
 Edit a parsed tree in place
*****************************

The structural edits move nodes within a tree and adopt nodes from another. ``unwrap`` replaces an element with its
children and ``decompose`` drops a subtree:

.. testcode::

    doc = turbohtml.parse("<p>keep <b>bold</b> <span>drop me</span></p>")
    p = doc.find("p")
    print(doc.find("b").unwrap())
    doc.find("span").decompose()
    print(p.html)

.. testoutput::

    Element('b')
    <p>keep bold </p>

*********************************
 Rewrite an element's attributes
*********************************

``element.attrs`` is a live mapping, so assignment and deletion rewrite the element directly:

.. testcode::

    link = turbohtml.parse('<a href="/old" class="x" data-tmp="1">go</a>').find("a")
    link.attrs["href"] = "/new"
    link.attrs["class"] = ["btn", "primary"]
    del link.attrs["data-tmp"]
    print(link.html)

.. testoutput::

    <a href="/new" class="btn primary">go</a>

***********************************
 Merge adjacent text after editing
***********************************

Edits can leave a run of adjacent text nodes; :meth:`~turbohtml.Element.normalize` merges each run into one and drops
empty text nodes, throughout the subtree (the DOM operation `BeautifulSoup
<https://www.crummy.com/software/BeautifulSoup/>`_ spells ``smooth``):

.. testcode::

    from turbohtml import Text
    p = turbohtml.Element("p")
    p.extend([Text("Hello "), Text(""), Text("world")])
    p.normalize()
    print((len(p), p.html))

.. testoutput::

    (1, '<p>Hello world</p>')

******************************
 Duplicate or cache a subtree
******************************

Any node deep-copies into a fresh standalone tree, so a clone is independent of the original. Use
:func:`python:copy.deepcopy` to duplicate in memory, or :mod:`python:pickle` to cross a process or cache boundary; both
preserve processing instructions and CDATA sections exactly:

.. testcode::

    import copy
    menu = turbohtml.parse("<ul><li>tea</li></ul>").find("ul")
    clone = copy.deepcopy(menu)
    clone.append(turbohtml.Element("li"))
    print((menu.html, clone.html))

.. testoutput::

    ('<ul><li>tea</li></ul>', '<ul><li>tea</li><li></li></ul>')

*********************************
 Turn URLs and emails into links
*********************************

To linkify user-entered text the way `bleach.linkify <https://github.com/mozilla/bleach>`_ did, use
:func:`turbohtml.linkify.linkify`. It parses the HTML, so it links only in text the reader sees, never inside an
existing ``<a>``, a ``<script>``, or a tag you list in ``skip_tags``. Email autolinking is behind ``parse_email``
because not every page wants it. The default ``nofollow`` callback marks web links, and leaves a ``mailto:`` link alone:

.. testcode::

    from turbohtml.linkify import linkify

    print(linkify("email bob@example.com or visit https://example.com", parse_email=True))

.. testoutput::

    email <a href="mailto:bob@example.com">bob@example.com</a> or visit <a href="https://example.com" rel="nofollow">https://example.com</a>
