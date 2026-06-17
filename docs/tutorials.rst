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

The incomplete ``<sp`` stayed buffered until the rest of the tag arrived. That is the whole tokenizer API. Head to the
:doc:`how-to` guides for task-focused recipes, including porting an existing :class:`python:html.parser.HTMLParser`
subclass, or the :doc:`reference` for the exact signatures.

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

A list value for a token-list attribute (``class``, ``rel``, ...) joins on a space, and a ``None`` value is a valueless
attribute:

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

That is the whole tree API. Head to the :doc:`how-to` guides for task-focused recipes, the :doc:`migration` guide if you
are coming from another HTML library, or the :doc:`reference` for the exact signatures.
