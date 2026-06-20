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

The quickest port keeps your subclass: :class:`turbohtml.html_parser.HTMLParser` is a drop-in base class with the same
``handle_*`` callbacks and ``feed``/``close`` methods, over the WHATWG-conformant tokenizer. Change the import and the
base class and the handlers fire as before:

.. testcode::

    from turbohtml.html_parser import HTMLParser

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

*************************************
 Parse a document arriving in chunks
*************************************

When a document arrives over a stream you do not have to buffer the whole thing before parsing. Feed each chunk to an
:class:`turbohtml.IncrementalParser` and call ``close()`` for the finished :class:`~turbohtml.Document`; the parser
holds only the bytes it has not yet consumed, never the whole source, and the result is identical to parsing the joined
string with :func:`turbohtml.parse`:

.. testcode::

    parser = turbohtml.IncrementalParser()
    for chunk in ("<ul><li>on", "e<li>two</", "ul>"):
        parser.feed(chunk)
    document = parser.close()
    print([item.text for item in document.find_all("li")])

.. testoutput::

    ['one', 'two']

``feed`` also accepts ``bytes``: a chunk is decoded with the parser's ``encoding`` (``utf-8`` by default), and a
multi-byte character split across two chunks is held back until the rest of its bytes arrive. As a context manager the
parser releases its work-in-progress when the block exits, so you can stop early without leaking the partial parse:

.. testcode::

    with turbohtml.IncrementalParser(encoding="utf-8") as parser:
        parser.feed("<p>caf".encode("utf-8"))
        parser.feed("é</p>".encode("utf-8"))
        document = parser.close()
    print(document.find("p").text)

.. testoutput::

    café

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

********************************
 Read and set form-field values
********************************

:attr:`~turbohtml.Element.field_value` is the control's value with form semantics: a textarea's text, an option's value,
or the selected option value(s) of a ``select`` (a ``list`` for a ``multiple`` select). Assigning writes it back, and
:attr:`~turbohtml.Element.checked` reads or sets a checkbox or radio (setting a radio to ``True`` clears the other
same-name radios in the form):

.. testcode::

    form = turbohtml.parse(
        "<form><input name=email value=a@b.c>"
        "<select name=plan><option value=free>Free<option value=pro selected>Pro</select>"
        "<input name=terms type=checkbox value=yes></form>"
    ).find("form")
    print(form.find("input", attrs={"name": "email"}).field_value)
    print(form.find("select").field_value)
    form.find("input", attrs={"name": "terms"}).checked = True

.. testoutput::

    a@b.c
    pro

**************************************
 Serialize a form to name/value pairs
**************************************

:meth:`~turbohtml.Element.form_data` returns the form's successful controls as ``(name, value)`` pairs in document
order, following the WHATWG submission rules: unnamed, disabled, button, and unchecked checkbox/radio controls are
skipped, and a ``select`` contributes one pair per selected option. Pass the result straight to
:func:`urllib.parse.urlencode`:

.. testcode::

    from urllib.parse import urlencode

    print(form.form_data())
    print(urlencode(form.form_data()))

.. testoutput::

    [('email', 'a@b.c'), ('plan', 'pro'), ('terms', 'yes')]
    email=a%40b.c&plan=pro&terms=yes

****************************************
 Inspect the parse errors of a document
****************************************

:func:`turbohtml.parse` recovers from malformed markup the way a browser does and records each WHATWG parse error it
recovered from on :attr:`~turbohtml.Document.errors`. Each :class:`~turbohtml.ParseError` carries the spec ``code`` and
the source position (1-based ``line``, 0-based ``col``); a well-formed document yields an empty list:

.. testcode::

    import turbohtml
    document = turbohtml.parse("<a b b>")
    for error in document.errors:
        print(f"{error.code} at {error.line}:{error.col}")

.. testoutput::

    duplicate-attribute at 1:6

To fail instead of recover -- in a linter or a strict ingest pipeline -- pass ``strict=True`` and catch
:class:`~turbohtml.HTMLParseError`, whose ``error`` attribute is the first :class:`~turbohtml.ParseError`:

.. testcode::

    try:
        turbohtml.parse("<!DOCTYPE", strict=True)
    except turbohtml.HTMLParseError as exception:
        print(exception.error.code)

.. testoutput::

    eof-in-doctype

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
attribute selectors with the ``=``, ``~=``, ``|=``, ``^=``, ``$=``, ``*=`` operators, the tree-structural pseudo-classes
(``:root``, ``:empty``, ``:first-child``, ``:last-child``, ``:only-child``, their ``-of-type`` variants, and the
``:nth-child()`` family with the ``An+B`` microsyntax, and the Level-4 ``of S`` clause that filters the sibling list by
a selector), joined by the descendant, child (``>``), adjacent (``+``), and general-sibling (``~``) combinators, with
comma groups:

.. testcode::

    import turbohtml
    doc = turbohtml.parse('<ul><li class=on>a<li><a href="/x">b</a></ul>')
    print([li.text for li in doc.select("li.on")])
    print(doc.select_one('a[href^="/"]').text)
    print([li.text for li in doc.select("li:nth-child(odd)")])

.. testoutput::

    ['a']
    b
    ['a']

``:nth-child(An+B of S)`` counts only the inclusive siblings that match the selector list ``S``, so ``An+B`` indexes
that filtered subset rather than every sibling -- here the second ``.row`` item, skipping the separator between them:

.. testcode::

    table = turbohtml.parse(
        "<ul><li class=row>a</li><li class=sep>-</li>"
        "<li class=row>b</li><li class=row>c</li></ul>"
    )
    print([li.text for li in table.select("li:nth-child(2 of .row)")])

.. testoutput::

    ['b']

The Selectors Level 4 functional pseudo-classes are supported too: ``:is()`` and ``:where()`` match an element against a
nested selector list (they differ only in specificity, which a tree matcher ignores), ``:has()`` keeps an element when a
relative selector finds a match anchored at it, and ``:not()`` keeps an element that matches none of its arguments.
``:not()`` takes a full selector list, so it negates compound and complex selectors -- not just a single class or type
-- and nests with the others (``article:not(:has(img))`` selects the image-less articles):

.. testcode::

    page = turbohtml.parse(
        '<article><h1>Post</h1><figure><img></figure></article>'
        "<article><h1>Note</h1></article>"
    )
    print([a.select_one("h1").text for a in page.select("article:has(img)")])
    print([e.tag for e in page.select(":is(h1, figure)")])
    print([a.select_one("h1").text for a in page.select("article:not(:has(img))")])

.. testoutput::

    ['Post']
    ['h1', 'figure', 'h1']
    ['Note']

The form and UI pseudo-classes select controls by the state the markup pins down: ``:checked``, ``:disabled`` /
``:enabled``, ``:required`` / ``:optional``, ``:read-only`` / ``:read-write``, and ``:default``. ``:lang()`` matches the
nearest ``lang`` attribute (with hyphen-prefix ranges, so ``:lang(en)`` also matches ``en-GB``) and ``:dir()`` the
resolved text direction. ``:scope`` is the element the query is rooted at, which anchors a relative selector:

.. testcode::

    form = turbohtml.parse(
        "<form><input name=agree type=checkbox checked>"
        "<input name=email required><input name=token disabled></form>"
    )
    print([e.attrs["name"] for e in form.select(":checked")])
    print([e.attrs["name"] for e in form.select(":required")])
    page = turbohtml.parse("<p lang=en-GB>hi</p><p lang=fr>salut</p>")
    print([p.text for p in page.select(":lang(en)")])
    card = turbohtml.parse("<div id=card><h2>T</h2><p>body</p></div>").select_one("#card")
    print([e.tag for e in card.select(":scope > p")])

.. testoutput::

    ['agree']
    ['email']
    ['hi']
    ['p']

The interaction- and navigation-state pseudo-classes -- ``:hover``, ``:focus``, ``:focus-within``, ``:focus-visible``,
``:active``, ``:target``, ``:target-within``, ``:visited``, ``:link``, and ``:any-link`` -- parse as valid selectors but
match nothing, since a parsed tree has no live UA state. They stay usable inside ``:is()`` and ``:not()`` rather than
raising, so ``a:not(:visited)`` keeps every link.

``:is()`` and ``:where()`` take a *forgiving* selector list: an arm that fails to parse is dropped and the rest stay
usable, so one unsupported or malformed arm never invalidates the whole selector (``:not()`` and ``:has()`` take a real
list, where a bad arm is still an error):

.. testcode::

    doc = turbohtml.parse("<p>one</p><div>two</div>")
    print([e.tag for e in doc.select(":is(p, :totally-unknown)")])

.. testoutput::

    ['p']

``#id`` and ``.class`` selectors compare case-sensitively in a standards-mode document and ASCII case-insensitively in a
quirks-mode one (a document with no doctype), matching how a browser resolves them. Add a ``<!doctype html>`` to keep
the comparison exact:

.. testcode::

    markup = '<div class="Lead" id="Main">x</div>'
    print(turbohtml.parse(markup).select_one(".lead").tag)  # quirks: folds case
    print(turbohtml.parse("<!doctype html>" + markup).select_one(".lead"))  # standards: exact

.. testoutput::

    div
    None

``:empty`` follows Selectors Level 4: an element counts as empty when its only children are comments or document white
space, so a blank item matches while one holding a non-breaking space (``&nbsp;`` is not white space) does not:

.. testcode::

    items = turbohtml.parse("<ul><li> </li><li>&nbsp;</li><li><!--TODO--></li><li>x</li></ul>")
    print([li.text for li in items.select("li:empty")])

.. testoutput::

    [' ', '']

To test a node you already hold rather than search beneath it, use :meth:`~turbohtml.Node.matches` (does this node
match) or :meth:`~turbohtml.Node.closest` (the nearest matching self-or-ancestor):

.. testcode::

    link = turbohtml.parse('<nav><a href="/x">home</a></nav>').select_one("a")
    print(link.matches("nav a"))
    print(link.closest("nav").tag)

.. testoutput::

    True
    nav

****************************
 Chain queries like pyquery
****************************

For code migrating off pyquery's jQuery-style chaining, :class:`turbohtml.query.Query` wraps a set of elements and every
traversal and mutation method returns a new wrapper, so calls compose. The method names are turbohtml's own (so
``add_class`` rather than ``addClass``), but the structure carries over:

.. testcode::

    from turbohtml.query import Query

    page = Query("<ul><li class=x>a</li><li>b</li><li class=x>c</li></ul>")
    print(page("li").filter(".x").eq(0).add_class("first").attr("class"))
    print(page("li").text())

.. testoutput::

    x first
    a b c

******************
 Query with XPath
******************

:meth:`~turbohtml.Node.xpath` evaluates an XPath 1.0 expression relative to a node and returns a list for a node-set
(elements as nodes, attribute and ``text()`` values as ``str``, in document order), or the matching ``float`` / ``str``
/ ``bool`` for a scalar expression like ``count(...)`` or ``string(...)``. :meth:`~turbohtml.Node.xpath_one` returns the
first result or ``None``, and :meth:`~turbohtml.Node.xpath_iter` returns an iterator. The engine supports the structural
axes, the ``name`` / ``*`` / ``node()`` / ``text()`` / ``comment()`` / ``processing-instruction()`` node tests,
predicates, the boolean, relational, and arithmetic operators, unions, and the complete XPath 1.0 core function library:

.. testcode::

    import turbohtml
    doc = turbohtml.parse("<table><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>")
    print([td.text for td in doc.xpath("//td")])
    print(doc.xpath("//tr[2]/td[1]/text()"))
    print(doc.xpath("count(//td)"))
    print(doc.xpath_one("//td[. = '3']").text)

.. testoutput::

    ['1', '2', '3', '4']
    ['3']
    4.0
    3

An absolute path starts at the document root and a leading ``//`` rescans the whole document, so write ``.//`` for
descendants of the context node. Migrating from ``lxml``, ``parsel``, or ``pyquery`` keeps your existing expressions.

Two functions read the HTML document the way HTML means it, where ``lxml``'s legacy HTML parser returns nothing:
``lang()`` honors the HTML ``lang`` attribute (``lxml`` only consults ``xml:lang``), and ``namespace-uri()`` reports the
real SVG and MathML namespace for foreign content (``lxml`` leaves it empty). HTML elements report no namespace in both,
so an unprefixed name test keeps matching them.

Pass ``$name`` variables as keyword arguments instead of formatting values into the expression string, so a value with
quotes or special characters cannot break the query:

.. testcode::

    import turbohtml
    doc = turbohtml.parse("<a href='/in'>in</a><a href='/out'>out</a>")
    print([a.text for a in doc.xpath("//a[@href=$href]", href="/out")])

.. testoutput::

    ['out']

The EXSLT ``re:test`` and ``re:replace`` functions ``parsel`` and ``scrapy`` rely on work without registering a
namespace; the ``re:`` prefix dispatches to Python's :mod:`re`:

.. testcode::

    import turbohtml
    doc = turbohtml.parse("<a href='/p/12'>a</a><a href='/q'>b</a>")
    print([a.attrs["href"] for a in doc.xpath(r"//a[re:test(@href, '\d')]")])

.. testoutput::

    ['/p/12']

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
:class:`~turbohtml.Formatter`, and ``layout`` selects the whitespace. The default ``None`` gives the compact form; a
:class:`~turbohtml.Indent` (an int for that many spaces, or a string used verbatim) switches to a pretty form that adds
whitespace and so does not preserve meaning. :meth:`~turbohtml.Node.encode` is the same but returns bytes:

.. testcode::

    import turbohtml
    from turbohtml import Formatter, Indent
    card = turbohtml.parse("<div><p>café &amp; co</p></div>").select_one("div")
    print(card.inner_html)
    print(card.serialize(formatter=Formatter.NAMED_ENTITIES))
    print(card.serialize(layout=Indent(2)))
    print(card.encode("ascii", formatter=Formatter.NAMED_ENTITIES))

.. testoutput::

    <p>café &amp; co</p>
    <div><p>caf&eacute; &amp; co</p></div>
    <div>
      <p>
        café &amp; co
      </p>
    </div>
    b'<div><p>caf&eacute; &amp; co</p></div>'

*******************
 Minify the output
*******************

Pass ``layout=`` a :class:`~turbohtml.Minify` to ``serialize`` (or ``encode``) to shrink the markup. Every transform is
round-trip safe: the minified bytes reparse to the same tree, so minifying never changes meaning. The four flags fold
insignificant whitespace, omit the start/end tags the WHATWG rules make optional, drop redundant attribute quotes, and
strip comments; all default on. Because ``layout`` holds one mode, a :class:`~turbohtml.Minify` and an
:class:`~turbohtml.Indent` cannot be combined.

.. testcode::

    import turbohtml
    from turbohtml import Minify
    doc = turbohtml.parse(
        "<html><head><title>Hi</title></head>"
        "<body><p class='lead'>one</p>  <p>two</p><!--note--></body></html>"
    )
    print(doc.serialize(layout=Minify()))
    print(doc.serialize(layout=Minify(omit_optional_tags=False, collapse_whitespace=False)))

.. testoutput::

    <title>Hi</title><p class=lead>one</p> <p>two
    <html><head><title>Hi</title></head><body><p class=lead>one</p>  <p>two</p></body></html>

Whitespace-significant elements (``pre``, ``textarea``, ``listing``) and raw-text elements (``script``, ``style``) keep
their content verbatim, and a tag is never dropped when omitting it would let the reparse reconstruct a formatting
element across the boundary.

***********************************
 Normalize attributes and encoding
***********************************

Two more keyword options on ``serialize`` and ``encode`` normalize the output without touching the tree, and compose
with any ``formatter`` or ``layout``. ``sort_attributes`` emits each start tag's attributes in ascending name order, so
two serializations of equal trees diff cleanly:

.. testcode::

    import turbohtml
    node = turbohtml.parse("<p id=main class=lead data-x=1>hi</p>").select_one("p")
    print(node.serialize(sort_attributes=True))

.. testoutput::

    <p class="lead" data-x="1" id="main">hi</p>

``meta_charset`` makes the document ``<head>`` declare the output encoding: an existing ``<meta charset>`` (or ``<meta
http-equiv="content-type">``) is normalized in place, and a head that declares none gets a ``<meta charset>`` injected
as its first child — never a duplicate. ``serialize`` declares ``utf-8`` (the encoding of the returned ``str``), while
``encode`` declares the encoding it writes:

.. testcode::

    import turbohtml
    doc = turbohtml.parse("<title>Hi</title>")
    print(doc.serialize(meta_charset=True))
    print(doc.encode("iso-8859-1", meta_charset=True))

.. testoutput::

    <html><head><meta charset="utf-8"><title>Hi</title></head><body></body></html>
    b'<html><head><meta charset="iso-8859-1"><title>Hi</title></head><body></body></html>'

********************
 Export to Markdown
********************

:meth:`~turbohtml.Node.to_markdown` renders a node and its subtree as GitHub-Flavored Markdown — headings, lists, links,
emphasis, code, blockquotes, images, and pipe tables — collapsing runs of whitespace the way normal flow lays them out.
It is a one-call replacement for the ``scrape`` → ``Markdown`` step that html2text or markdownify would do, with no
second dependency and the whole walk in C:

.. testcode::

    import turbohtml
    page = turbohtml.parse(
        "<h1>Recipe</h1><p>A <b>quick</b> loaf.</p>"
        "<ul><li>flour</li><li>water</li></ul>"
        "<blockquote><p>Rest 1 hour.</p></blockquote>"
    )
    print(page.to_markdown())

.. testoutput::

    # Recipe

    A **quick** loaf.

    - flour
    - water

    > Rest 1 hour.

Call it on any node to export just that subtree (``article.to_markdown()``). The output is opinionated GFM: ATX
headings, ``-`` bullets, fenced code blocks, inline links, and ``*``/``**`` emphasis.

Keyword options cover the markdownify and html2text configuration surface, so a migration reproduces the old output:
setext headings, underscore emphasis, reference links, padded tables, alternate escaping, and more. The :doc:`migration`
guide maps each old option to its turbohtml name.

.. testcode::

    doc = turbohtml.parse('<h2>Tea</h2><p><b>Steep</b> it. <a href="/x">More</a>.</p>')
    print(doc.to_markdown(heading_style="setext", strong="__", link_style="reference"))

.. testoutput::

    Tea
    ---

    __Steep__ it. [More][1].

    [1]: /x

Three output modes shape the result further. ``wrap_width`` word-wraps prose at a column (``0``, the default, leaves
paragraphs unwrapped), honoring list and blockquote indentation; ``wrap_list_items`` extends wrapping into list items
and ``wrap_links=False`` keeps a ``[text](url)`` construct on one line. ``image_mode="html"`` and ``table_mode="html"``
pass the original ``<img>`` or ``<table>`` through verbatim, for readers that render embedded HTML. ``transliterate``
folds common non-ASCII typography in prose -- smart quotes, dashes, ellipsis, accented letters -- to ASCII:

.. testcode::

    doc = turbohtml.parse("<p>The “quick” brown fox — jumps over the lazy dog today.</p>")
    print(doc.to_markdown(wrap_width=30, transliterate=True))

.. testoutput::

    The "quick" brown fox -- jumps
    over the lazy dog today.

To convert a Google Docs HTML export, pass ``google_doc=True`` so the inline-CSS styling it carries (font weight, font
style, fixed-width fonts, and ``margin-left`` list nesting) turns into Markdown:

.. testcode::

    export = '<p><span style="font-weight:700">Bold</span> and <span style="font-style:italic">soft</span>.</p>'
    print(turbohtml.parse(export).to_markdown(google_doc=True))

.. testoutput::

    **Bold** and *soft*.

When an option cannot express the rule you need -- a custom element, or a tag that should render its own way -- pass
``converters``: a mapping from a lowercased tag name to a ``callable(element, content) -> str``. The callable receives
the :class:`~turbohtml.Element` and the already-converted Markdown of its children, and returns the Markdown for that
element. A registered tag's built-in rendering is replaced; every other tag is untouched, and the hook costs nothing
when the mapping is omitted.

.. testcode::

    html = '<p>Watch <video src="/clip.mp4">a clip</video> and <abbr title="Markdown">MD</abbr>.</p>'
    converters = {
        "video": lambda el, content: f"[{content}]({el.attrs['src']})",
        "abbr": lambda el, content: f"{content} ({el.attrs['title']})",
    }
    print(turbohtml.parse(html).to_markdown(converters=converters))

.. testoutput::

    Watch [a clip](/clip.mp4) and MD (Markdown).

Return ``""`` to drop an element, or return ``content`` unchanged to unwrap it. A registered block-level tag is laid out
on its own line; any other tag flows inline. The callable runs inside the same per-tree lock the walk holds, so it may
read the element's attributes and subtree freely.

**********************
 Export to plain text
**********************

:meth:`~turbohtml.Node.to_text` renders layout-aware plain text — the role `inscriptis
<https://github.com/weblyzard/inscriptis>`_ fills — keeping the visual structure rather than collapsing everything like
:attr:`~turbohtml.Node.text` does. Its most visible feature is laying tables out as aligned columns:

.. testcode::

    import turbohtml
    page = turbohtml.parse(
        "<h2>Stock</h2>"
        "<table><tr><th>Item</th><th>Qty</th></tr>"
        "<tr><td>Apples</td><td>3</td></tr><tr><td>Pears</td><td>40</td></tr></table>"
    )
    print(page.to_text())

.. testoutput::

    Stock

    Item    Qty
    Apples  3
    Pears   40

Links are hidden by default; pass ``links="inline"`` to append ``text (url)``, ``links="footnote"`` for numbered
references, ``images=True`` to show alt text, and ``width`` to word-wrap.

********************************
 Label spans of the export text
********************************

To pull labeled regions out of the rendered text -- the role inscriptis fills with ``annotation_rules`` -- call
:meth:`~turbohtml.Node.to_annotated_text` with a rule mapping. It returns the same text :meth:`~turbohtml.Node.to_text`
would, plus a list of ``(start, end, label)`` triples whose offsets index into that text, and it accepts every
``to_text`` option as well:

.. testcode::

    import turbohtml
    text, labels = turbohtml.parse("<h1>Q3</h1><p>Up <b>12%</b> on the year.</p>").to_annotated_text(
        {"h1": ["heading"], "b": ["metric"]}
    )
    print(text)
    for start, end, label in labels:
        print(label, "->", repr(text[start:end]))

.. testoutput::

    Q3

    Up 12% on the year.
    heading -> 'Q3'
    metric -> '12%'

A rule key is a tag (``"b"``), a ``tag#attr`` requiring the attribute, a ``tag#attr=value`` matching one
whitespace-separated token, or the tag-less ``#attr`` / ``#attr=value`` to match across any tag; its value is the list
of labels to attach.

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

*********************************
 Find where an element came from
*********************************

Every parsed element records where its start tag began, so an error report or linter can point back at the source. Read
:attr:`~turbohtml.Node.source_line` (1-based), :attr:`~turbohtml.Node.source_col` (0-based), or the
:attr:`~turbohtml.Node.position` pair -- the same convention as :meth:`python:html.parser.HTMLParser.getpos` and
``lxml``'s ``sourceline``:

.. testcode::

    doc = turbohtml.parse("<ul>\n  <li>first</li>\n  <li>second</li>\n</ul>")
    for item in doc.find_all("li"):
        print(item.text, item.position)

.. testoutput::

    first (2, 2)
    second (3, 2)

An element with no place in the source -- a node built by hand, or an implied ``html``/``head``/``body`` -- reads
``None``. Pass ``positions=False`` to :func:`turbohtml.parse` to skip the tracking entirely (a small memory and speed
saving), after which every accessor reads ``None``:

.. testcode::

    print(turbohtml.parse("<p>x</p>", positions=False).find("p").source_line)
    print(turbohtml.Element("div").position)

.. testoutput::

    None
    None

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

By default the callbacks only see freshly detected links; pass ``process_existing=True`` to also run them over ``<a>``
tags already in the input. A callback reads ``link.existing`` to tell an author's anchor from a detected one, and
returning ``None`` for an existing anchor unwraps it to its text. Use ``extra_tlds`` to link bare domains on a private
suffix the IANA table does not know, and ``schemes`` to autolink only an allowlist of explicit URL schemes:

.. testcode::

    from turbohtml.linkify import Link, linkify


    def annotate(link: Link) -> Link:
        link.attrs["data-seen"] = "author" if link.existing else "auto"
        return link


    html = '<a href="https://docs.example">docs</a>, ping app.internal, skip ftp://x.example'
    print(linkify(html, callbacks=[annotate], process_existing=True, extra_tlds=["internal"], schemes=["https"]))

.. testoutput::

    <a href="https://docs.example" data-seen="author">docs</a>, ping <a href="http://app.internal" data-seen="auto">app.internal</a>, skip ftp://x.example

**************************
 Find links in plain text
**************************

When the text is not HTML and you only need *where* the links are -- to highlight them, count them, or build your own
markup -- use :class:`turbohtml.linkify.Detector`. ``find`` returns a :class:`~turbohtml.linkify.LinkSpan` per match,
with offsets, the matched text, and the normalized ``url``; ``has_link`` answers the yes/no question more cheaply:

.. testcode::

    from turbohtml.linkify import Detector

    detector = Detector()
    for span in detector.find("ping bob@example.com about example.com"):
        print(span.start, span.end, span.url)

.. testoutput::

    5 20 mailto:bob@example.com
    27 38 http://example.com

Register custom ``tlds`` to detect bare domains on an internal suffix, and scheme-less ``schemes`` such as ``tel`` so
their opaque URLs are found too (every ``scheme://`` URL is detected without registration):

.. testcode::

    detector = Detector(tlds=["corp"], schemes=["tel"])
    print([span.url for span in detector.find("wiki.corp or tel:+1-800-555-0100")])

.. testoutput::

    ['http://wiki.corp', 'tel:+1-800-555-0100']

*************************
 Sanitize untrusted HTML
*************************

To clean user-submitted HTML the way ``bleach.clean`` did, use :func:`turbohtml.sanitizer.sanitize`. A
:class:`~turbohtml.sanitizer.Policy` says what to keep -- here the ``relaxed`` preset for typical user content -- and a
non-overridable baseline drops scripting and ``javascript:`` URLs no matter what the policy allows:

.. testcode::

    from turbohtml.sanitizer import sanitize, Policy

    print(sanitize("<p>Hi <a href='javascript:alert(1)'>link</a></p><script>evil()</script>", Policy.relaxed()))

.. testoutput::

    <p>Hi <a>link</a></p>&lt;script&gt;evil()&lt;/script&gt;
