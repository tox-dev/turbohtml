###########################
 Select and query elements
###########################

*************************************
 Pull strings out of a page (parsel)
*************************************

Scraping code wants *strings*, not nodes. :meth:`~turbohtml.Element.attr` reads one attribute as a single string (or a
default when it is missing), and :meth:`~turbohtml.Node.re` / :meth:`~turbohtml.Node.re_first` run a regular expression
over a node's text, the same extraction primitives Scrapy's ``parsel`` offers. ``re`` returns the one capturing group
when the pattern has exactly one, otherwise the whole match, so a single pattern pulls the part you want:

.. testcode::

    doc = turbohtml.parse('<p>Order 1138 shipped</p><a href="/p/42">item 42</a>')
    print(doc.select_one("p").re_first(r"Order (\d+)"))
    print(doc.select_one("a").attr("href"))
    print(doc.select_one("a").re(r"/p/(\d+)", attr="href"))

.. testoutput::

    1138
    /p/42
    ['42']

Pass ``attr=`` to run the pattern over an attribute value instead of the text; an absent attribute yields ``[]`` from
``re`` and the default from ``re_first``. Map the call across a :meth:`~turbohtml.Node.select` result to extract from
every match at once:

.. testcode::

    listing = turbohtml.parse('<a href="/p/1">a</a><a href="/p/2">b</a>')
    print([a.re_first(r"\d+", attr="href") for a in listing.select("a")])

.. testoutput::

    ['1', '2']

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
that filtered subset rather than every sibling. Here that picks the second ``.row`` item, skipping the separator between
them:

.. testcode::

    table = turbohtml.parse("<ul><li class=row>a</li><li class=sep>-</li><li class=row>b</li><li class=row>c</li></ul>")
    print([li.text for li in table.select("li:nth-child(2 of .row)")])

.. testoutput::

    ['b']

The Selectors Level 4 functional pseudo-classes are supported too: ``:is()`` and ``:where()`` match an element against a
nested selector list (they differ only in specificity, which a tree matcher ignores), ``:has()`` keeps an element when a
relative selector finds a match anchored at it, and ``:not()`` keeps an element that matches none of its arguments.
``:not()`` takes a full selector list, so it negates compound and complex selectors (not just a single class or type)
and nests with the others (``article:not(:has(img))`` selects the image-less articles):

.. testcode::

    page = turbohtml.parse("<article><h1>Post</h1><figure><img></figure></article><article><h1>Note</h1></article>")
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
        "<form><input name=agree type=checkbox checked><input name=email required><input name=token disabled></form>"
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

The interaction- and navigation-state pseudo-classes (``:hover``, ``:focus``, ``:focus-within``, ``:focus-visible``,
``:active``, ``:target``, ``:target-within``, ``:visited``, ``:link``, and ``:any-link``) parse as valid selectors but
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

**************************************
 Trim a parsed document to a selector
**************************************

:meth:`~turbohtml.Node.prune` keeps only the descendants matching a CSS selector, plus their ancestors up to the node it
is called on and the whole subtree under each match, and removes everything else in place. This is the parse-then-keep
pattern BeautifulSoup spelled with a ``SoupStrainer``: parse the whole document the WHATWG way, then shrink it to the
part you care about. It returns the node, so it chains off :func:`~turbohtml.parse`:

.. testcode::

    markup = "<body><nav>skip</nav><main><article>keep<b>me</b></article><aside>drop</aside></main></body>"
    doc = turbohtml.parse(markup).prune("article")
    print(doc.select_one("body").serialize())

.. testoutput::

    <body><main><article>keep<b>me</b></article></main></body>

The ancestors of a match stay so the match keeps its place in the tree, and the match's own subtree stays whole, so the
``<b>`` and the text survive while the unrelated ``<nav>`` and ``<aside>`` go. A selector that matches nothing empties
the subtree.

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

To select foreign content by namespace, bind each prefix to a URI through ``namespaces`` (the same argument ``lxml``
takes). A prefixed name test then matches an element whose namespace equals the bound URI and whose local name equals
the suffix, so ``//svg:rect`` finds the SVG rectangle while the unprefixed ``//rect`` keeps matching by name alone:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<svg><rect/><circle/></svg>")
    rects = doc.xpath("//svg:rect", namespaces={"svg": "http://www.w3.org/2000/svg"})
    print(len(rects))

.. testoutput::

    1

Pass ``$name`` variables as keyword arguments instead of formatting values into the expression string, so a value with
quotes or special characters cannot break the query:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<a href='/in'>in</a><a href='/out'>out</a>")
    print([a.text for a in doc.xpath("//a[@href=$href]", href="/out")])

.. testoutput::

    ['out']

A variable can also hold a node-set: bind an :class:`~turbohtml.Element` or any iterable of elements (a prior
:meth:`~turbohtml.Node.xpath` result) and feed it back into a later expression instead of re-walking the tree. The
node-set joins path steps, ``count()``, unions, and predicates like any other node-set:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>")
    rows = doc.xpath("//tr")
    print([td.text for td in doc.xpath("$rows/td", rows=rows)])
    print(doc.xpath("count($rows)", rows=rows))

.. testoutput::

    ['a', 'b', 'c']
    2.0

The EXSLT ``re:test`` and ``re:replace`` functions ``parsel`` and ``scrapy`` rely on work without registering a
namespace; the ``re:`` prefix dispatches to Python's :mod:`re`:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<a href='/p/12'>a</a><a href='/q'>b</a>")
    print([a.attrs["href"] for a in doc.xpath(r"//a[re:test(@href, '\d')]")])

.. testoutput::

    ['/p/12']

Register your own functions under ``extensions={(namespace, name): callable}`` (use ``None`` for the namespace to call
the function unprefixed). The callable receives a context whose ``context_node`` is the current element, then the
evaluated arguments: a node-set arrives as a ``list`` of :class:`~turbohtml.Element`, and a string, number, or boolean
as the matching Python scalar. Returning a ``str``, number, or ``bool`` produces a scalar; returning an element or an
iterable of elements produces a node-set that feeds later path steps and predicates, exactly as in ``lxml``:

.. testcode::

    import turbohtml
    from types import SimpleNamespace
    from turbohtml import Element


    def first_two(context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
        return nodes[:2]


    doc = turbohtml.parse("<ul><li>a</li><li>b</li><li>c</li></ul>")
    result = doc.xpath("first_two(//li)/text()", extensions={(None, "first_two"): first_two})
    print(result)

.. testoutput::

    ['a', 'b']

Every element in a returned node-set must belong to the document being queried; returning one from another parse raises
``ValueError``.

When one expression runs over many nodes or documents -- a scraper looping rows, or one query across a corpus -- compile
it once with :class:`turbohtml.XPath` instead of re-parsing it on every :meth:`~turbohtml.Node.xpath` call. Bind
``smart_strings`` and ``extensions`` at construction, then call the object with a context node and any ``$name``
variables; it returns exactly what the matching :meth:`~turbohtml.Node.xpath` call would. The compiled object is
immutable and re-entrant, so one instance can be shared across threads.

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<table><tr><td class='num'>1</td><td>x</td></tr><tr><td class='num'>2</td></tr></table>")
    cells = turbohtml.XPath(".//td[@class=$cls]")
    for row in doc.xpath("//tr"):
        print([td.text for td in cells(row, cls="num")])

.. testoutput::

    ['1']
    ['2']

The same built-in dispatch covers the EXSLT ``set:``, ``str:``, ``math:``, and ``date:`` namespaces, so the node-set,
string, numeric, and date helpers ``lxml`` gets from ``libexslt`` work without registering anything. ``set:`` operates
on node-sets (``difference``, ``intersection``, ``distinct``, ``has-same-node``, ``leading``, ``trailing``), ``str:``
builds strings (``replace`` literal-not-regex, ``concat``, ``padding``, ``align``), ``math:`` reduces node-sets and
numbers (``min``, ``max``, ``highest``, ``lowest``, ``abs``, ``power``), and ``date:`` reads fields out of an ISO
``YYYY-MM-DD`` string (``year``, ``month-in-year``, ``day-in-month``, ``day-in-week``, ``leap-year``):

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<ul><li>3</li><li>1</li><li>1</li></ul>")
    print([li.text for li in doc.xpath("set:distinct(//li)")])
    print(doc.xpath("math:max(//li)"))
    print(doc.xpath("str:padding(3, '-')"))
    print(doc.xpath("date:year('2024-06-22')"))

.. testoutput::

    ['3', '1']
    3.0
    ---
    2024.0

``str:tokenize`` and ``str:split`` are not built in: they would have to synthesize token nodes, and the engine's
node-sets only reference nodes that already exist in the tree. Likewise ``date:`` reads an explicit date string rather
than the implicit current date-time, so a query stays deterministic. Register either through ``extensions=`` if you need
it.

************************
 Get the path to a node
************************

Once you have an element, ask it for a locator back to the document root, the way browser devtools "copy selector" and
lxml's ``getpath`` do. :meth:`~turbohtml.Element.css_path` returns a CSS selector and
:meth:`~turbohtml.Element.xpath_path` returns a positional XPath; both round-trip, so feeding the result to
:meth:`~turbohtml.Node.select` or :meth:`~turbohtml.Node.xpath` on the document returns exactly that element. Use them
to log a match, store a stable reference, or debug a scrape:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<body><div><p>one</p><p>two</p></div></body>")
    second = doc.select("p")[1]
    print(second.css_path())
    print(second.xpath_path())
    print(doc.select(second.css_path()) == [second])

.. testoutput::

    html > body > div > p:nth-of-type(2)
    /html/body/div/p[2]
    True

The CSS path anchors at the nearest ancestor (or the element itself) carrying a document-unique ``id``, which keeps it
short and stable against reordering; otherwise it descends from the root with ``:nth-of-type()`` steps. The XPath form
is always positional, like ``getpath``:

.. testcode::

    doc = turbohtml.parse('<body><main id="content"><ul><li>a</li><li>b</li></ul></main></body>')
    item = doc.select("li")[1]
    print(item.css_path())
    print(item.xpath_path())

.. testoutput::

    #content > ul > li:nth-of-type(2)
    /html/body/main/ul/li[2]

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

********************
 Find nodes by text
********************

``text`` matches an element against its collected text (every :class:`~turbohtml.Text` descendant concatenated, the same
string :attr:`~turbohtml.Node.text` returns). It takes the same kinds as the other filters except that a plain string is
the *whole* collected text rather than a substring: pass a compiled regex to search, or a callable predicate for
anything else. It composes with the tag, ``class_``, and attribute filters:

.. testcode::

    import re, turbohtml

    doc = turbohtml.parse(
        '<section><button class="buy">Add to cart</button><p>Price: $19</p><span>SKU-7788</span></section>'
    ).find("section")
    print(doc.find(text="Add to cart").tag)
    print([node.tag for node in doc.find_all(text=re.compile(r"\$\d+"))])
    print([node.tag for node in doc.find_all(text=lambda value: value.startswith("SKU"))])
    print(doc.find("button", text="Add to cart", class_="buy").tag)

.. testoutput::

    button
    ['p']
    ['span']
    button

To filter a literal ``text`` *attribute* (rather than the text content), pass it through ``attrs={"text": ...}``, since
the ``text`` keyword is the text predicate.

***********************************
 Read a table into rows or records
***********************************

:meth:`~turbohtml.Element.rows` reads a ``<table>`` into a list of rows, each a ``list[str]``, with ``rowspan`` and
``colspan`` resolved by filling every spanned cell, so the result is rectangular and you never resolve spans by hand:

.. testcode::

    import turbohtml

    table = turbohtml.parse(
        "<table>"
        "<tr><th>Region</th><th>Q1</th><th>Q2</th></tr>"
        "<tr><td rowspan=2>West</td><td>10</td><td>12</td></tr>"
        "<tr><td>8</td><td>9</td></tr>"
        "</table>"
    ).find("table")
    for row in table.rows():
        print(row)

.. testoutput::

    ['Region', 'Q1', 'Q2']
    ['West', '10', '12']
    ['West', '8', '9']

:meth:`~turbohtml.Element.records` keys the first row (the header, typically the ``thead`` row) over each later row as a
``list[dict]`` -- the shape a ``pandas.read_html`` user feeds straight to ``pandas.DataFrame``, with no pandas
dependency:

.. testcode::

    for record in table.records():
        print(record)

.. testoutput::

    {'Region': 'West', 'Q1': '10', 'Q2': '12'}
    {'Region': 'West', 'Q1': '8', 'Q2': '9'}

:meth:`~turbohtml.Node.tables` returns every table on the page, each as :meth:`~turbohtml.Element.rows`, so you can scan
a document without locating each ``<table>`` first:

.. testcode::

    document = turbohtml.parse("<table><tr><td>a</td></tr></table><table><tr><td>b</td><td>c</td></tr></table>")
    print(document.tables())

.. testoutput::

    [[['a']], [['b', 'c']]]
