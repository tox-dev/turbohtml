###########
 Tutorials
###########

*****************
 Getting started
*****************

Go from an empty environment to escaping and unescaping your first HTML.

Install turbohtml from PyPI:

.. code-block:: console

    $ pip install turbohtml

Open a Python prompt and escape some text for safe inclusion in an HTML page:

.. testcode::

    import turbohtml
    print(turbohtml.escape("5 > 3 & 2 < 4"))

.. testoutput::

    5 &gt; 3 &amp; 2 &lt; 4

By default ``escape`` escapes quotation marks too, which you want inside an attribute value:

.. testcode::

    print(turbohtml.escape("name=\"O'Brien\""))

.. testoutput::

    name=&quot;O&#x27;Brien&quot;

Reverse the process: turn HTML character references back into text:

.. testcode::

    print(turbohtml.unescape("Tom &amp; Jerry, caf&eacute;"))

.. testoutput::

    Tom & Jerry, café

Stay with the string helpers, or continue below to break whole documents into tokens.

********************************
 Tokenizing your first document
********************************

Go from a string of HTML to a stream of tokens you can inspect.

Start with a small document and hand it to :func:`turbohtml.tokenize`, which returns an iterator of
:class:`turbohtml.Token` objects:

.. testcode::

    import turbohtml
    for token in turbohtml.tokenize('<p class="intro">Tom &amp; Jerry</p>'):
        print(token)

.. testoutput::

    Token(START_TAG, tag='p')
    Token(TEXT, data='Tom & Jerry')
    Token(END_TAG, tag='p')

``type`` identifies each token as a :class:`turbohtml.TokenType`. Start and end tags carry the lowercased tag name and
the attributes, decoded:

.. testcode::

    start, text, end = turbohtml.tokenize('<p class="intro">Tom &amp; Jerry</p>')
    print(start.type)
    print(start.tag)
    print(start.attrs)

.. testoutput::

    1
    p
    [('class', 'intro')]

Text arrives with character references resolved (the ``&amp;`` above came through as a plain ``&``). Content that the
HTML specification treats as raw, such as a script body, arrives as one text token without further interpretation:

.. testcode::

    print([token.data for token in turbohtml.tokenize("<script>if (a < b) run()</script>")
           if token.type is turbohtml.TokenType.TEXT])

.. testoutput::

    ['if (a < b) run()']

When the document arrives in pieces (from a network stream, for example), create a :class:`turbohtml.Tokenizer` and feed
the pieces as they come. Each ``feed()`` returns the tokens that piece completed, and ``close()`` flushes whatever
remains:

.. testcode::

    tokenizer = turbohtml.Tokenizer()
    print([token.tag for token in tokenizer.feed("<div><sp")])
    print([token.tag for token in tokenizer.feed("an>")])
    print(list(tokenizer.close()))

.. testoutput::

    ['div']
    ['span']
    []

The incomplete ``<sp`` stayed buffered until the rest of the tag arrived. That is the whole tokenizer API. If you are
porting an existing :class:`python:html.parser.HTMLParser` subclass, :class:`turbohtml.migration.stdlib.HTMLParser`
keeps the same ``handle_*`` callbacks over this tokenizer, so the migration is changing the base class. Head to the
:doc:`how-to/index` guides for task-focused recipes or the :doc:`reference` for the exact signatures.

********************************
 Parsing a document into a tree
********************************

A token stream is flat. To see which element contains which, you need the *structure*: a tree. Go from a string of HTML
to a navigable tree of nodes.

.. important::

    The one rule worth learning first: turbohtml models text as real **child nodes** (the WHATWG DOM shape), not `lxml
    <https://lxml.de>`_'s ``text``/``tail`` or `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`_'s
    ``.string``. So ``node[i]`` indexes a node's children, and attributes are reached through ``node.attrs``, never
    ``node["attr"]``.

Hand a whole document to :func:`turbohtml.parse`. It applies the full WHATWG tree-construction algorithm (the same one
browsers run, including the error recovery that inserts the missing ``html``, ``head`` and ``body``) and returns a
:class:`turbohtml.Document`:

.. testcode::

    import turbohtml
    doc = turbohtml.parse("<h1>Hello</h1><p>Tom &amp; <a href='/x'>Jerry</a></p>")
    print(doc.root)

.. testoutput::

    Element('html')

The recovery is not silent: each WHATWG parse error turbohtml recovered from is on :attr:`~turbohtml.Document.errors`, a
list of :class:`~turbohtml.ParseError` with the spec ``code`` and source position. A clean document leaves it empty;
malformed input fills it (and ``parse(..., strict=True)`` raises :class:`~turbohtml.HTMLParseError` on the first one):

.. testcode::

    print(doc.errors)
    print(turbohtml.parse("<a b b>").errors[0].code)

.. testoutput::

    []
    duplicate-attribute

:meth:`~turbohtml.Node.find` returns the first descendant matching a tag (and any attributes you pass), or ``None``:

.. testcode::

    print(doc.find("a"))
    print(doc.find("a").attrs)

.. testoutput::

    Element('a')
    {'href': '/x'}

Every node exposes its text and its markup. :attr:`~turbohtml.Node.text` is the concatenated character data of the
subtree, with references decoded; :attr:`~turbohtml.Node.html` re-serializes the subtree:

.. testcode::

    paragraph = doc.find("p")
    print(paragraph.text)
    print(paragraph.html)

.. testoutput::

    Tom & Jerry
    <p>Tom &amp; <a href="/x">Jerry</a></p>

turbohtml models text as real child nodes (the WHATWG DOM shape), so a paragraph's children are its text runs and its
elements interleaved, in order. A node is a sequence of its children: iterate it, take its length, index into it:

.. testcode::

    print(list(paragraph))
    print(len(paragraph))
    print(paragraph[1])

.. testoutput::

    [Text('Tom & '), Element('a')]
    2
    Element('a')

From any node you can walk outward as well as inward: :attr:`~turbohtml.Node.parent`,
:attr:`~turbohtml.Node.next_sibling`, and the lazy :attr:`~turbohtml.Node.ancestors` and
:attr:`~turbohtml.Node.descendants` iterators:

.. testcode::

    link = doc.find("a")
    print(link.parent)
    print([node.tag for node in link.ancestors if isinstance(node, turbohtml.Element)])

.. testoutput::

    Element('p')
    ['p', 'body', 'html']

For richer queries, :meth:`~turbohtml.Node.select` takes a CSS selector and returns every matching descendant in
document order. The negation pseudo-class ``:not()`` keeps the elements that match none of its arguments; here, the
descendants of ``body`` that are not links:

.. testcode::

    print([node.tag for node in doc.select("body :not(a)")])

.. testoutput::

    ['h1', 'p']

Selectors also reach the form and UI pseudo-classes the markup determines, such as ``:checked`` for a checked control:

.. testcode::

    form = turbohtml.parse("<input type=checkbox checked><input type=checkbox>")
    print(len(form.select(":checked")))

.. testoutput::

    1

``:is()`` and ``:where()`` are forgiving, so an arm they cannot parse is dropped and the rest still select; a typo in
one alternative does not break the query:

.. testcode::

    print([node.tag for node in doc.select(":is(h1, :oops)")])

.. testoutput::

    ['h1']

Structural pseudo-classes count positions, and ``:nth-child(An+B of S)`` counts only the siblings matching ``S``; here
the first checked box, ignoring the unchecked ones in between:

.. testcode::

    boxes = turbohtml.parse("<p><input checked><input><input checked></p>")
    print([e.attrs.get("checked") for e in boxes.select("input:nth-child(1 of [checked])")])

.. testoutput::

    ['']

If you are coming from pyquery's jQuery-style chaining, :class:`turbohtml.query.Query` wraps these primitives in a
fluent, chainable surface where each call returns a new wrapper.

Because the node types are a sealed hierarchy, structural pattern matching works: each subtype unpacks its defining
field:

.. testcode::

    for node in paragraph:
        match node:
            case turbohtml.Element(tag):
                print("element", tag)
            case turbohtml.Text(data):
                print("text", repr(data))

.. testoutput::

    text 'Tom & '
    element a

*****************************
 Building and editing a tree
*****************************

Everything so far read a document that already existed. You can also build one. Construct nodes from their classes and
assemble them with :meth:`~turbohtml.Element.append`; the ``text`` setter fills an element with a single text child:

.. testcode::

    from turbohtml import Element, Comment
    article = turbohtml.Element("article", {"class": "post"})
    title = turbohtml.Element("h1")
    title.text = "Tea"
    article.append(title)
    article.append(Comment("draft"))
    print(article.html)

.. testoutput::

    <article class="post"><h1>Tea</h1><!--draft--></article>

A list value for a token-list attribute (``class``, ``rel``, ...) joins on a space, and ``None`` (or ``""``) sets an
empty attribute, which reads back as the empty string:

.. testcode::

    print(turbohtml.Element("input", {"class": ["a", "b"], "disabled": None}).html)

.. testoutput::

    <input class="a b" disabled="">

Editing a parsed tree uses the BeautifulSoup vocabulary (``insert_before``, ``replace_with``, ``wrap``, ``unwrap``,
``decompose``), and ``element.attrs`` is a live mapping you assign to. A node already in a tree moves; a node from
another tree is adopted by copy:

.. testcode::

    doc = turbohtml.parse("<p>keep <b>bold</b> <span>drop</span></p>")
    print(doc.find("b").unwrap())
    doc.find("span").decompose()
    doc.find("p").attrs["class"] = "lead"
    print(doc.find("p").html)

.. testoutput::

    Element('b')
    <p class="lead">keep bold </p>

Duplicate a subtree with :func:`python:copy.deepcopy` (or :mod:`python:pickle`); the clone is a standalone tree you can
edit without touching the original:

.. testcode::

    import copy
    clone = copy.deepcopy(article)
    clone.append(turbohtml.Element("footer"))
    print(clone.html == article.html)

.. testoutput::

    False

When you serialize, ``layout=`` a :class:`~turbohtml.Minify` shrinks the output without changing what it means: it folds
insignificant whitespace, omits optional tags, unquotes safe attributes, and strips comments, and the result reparses to
the same tree. Here the ``</li>`` tags stay because real whitespace separates the items:

.. testcode::

    from turbohtml import Minify
    page = turbohtml.parse("<ul>\n  <li>one</li>\n  <li>two</li>\n</ul>")
    print(page.find("ul").serialize(layout=Minify()))

.. testoutput::

    <ul> <li>one</li> <li>two</li> </ul>

****************
 Export to text
****************

Once you have the node you want, :meth:`~turbohtml.Node.to_markdown` turns it into GitHub-Flavored Markdown in one call,
so a scraping script ends with Markdown instead of a tag soup:

.. testcode::

    doc = turbohtml.parse("<article><h2>Tea</h2><p>Steep <em>green</em> tea for <b>3</b> minutes.</p></article>")
    print(doc.find("article").to_markdown())

.. testoutput::

    ## Tea

    Steep *green* tea for **3** minutes.

**********************
 Pull out the article
**********************

A real page wraps that prose in navigation, sidebars and footers. When you only want the article and do not know its
selector, :meth:`~turbohtml.Node.main_content` finds the dominant content element for you by scoring the tree, and
:meth:`~turbohtml.Node.main_text` hands you its text directly:

.. testcode::

    page = turbohtml.parse(
        "<nav><a href='/'>Home</a></nav>"
        "<main class='post'><h2>Tea</h2>"
        "<p>Steeping green tea for three minutes draws out its flavor without turning it bitter.</p></main>"
        "<footer>(c) 2026</footer>"
    )
    print(page.main_content().tag)
    print(page.main_text())

.. testoutput::

    main
    Tea

    Steeping green tea for three minutes draws out its flavor without turning it bitter.

That is the whole tree API. Head to the :doc:`how-to/index` guides for task-focused recipes, the :doc:`migration/index`
guide if you are coming from another HTML library, or the :doc:`reference` for the exact signatures.
