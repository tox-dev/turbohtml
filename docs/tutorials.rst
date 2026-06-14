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

.. code-block:: pycon

    >>> import turbohtml
    >>> turbohtml.escape("5 > 3 & 2 < 4")
    '5 &gt; 3 &amp; 2 &lt; 4'

By default ``escape`` escapes quotation marks too, which you want inside an attribute value:

.. code-block:: pycon

    >>> turbohtml.escape("name=\"O'Brien\"")
    'name=&quot;O&#x27;Brien&quot;'

Reverse the process: turn HTML character references back into text:

.. code-block:: pycon

    >>> turbohtml.unescape("Tom &amp; Jerry, caf&eacute;")
    'Tom & Jerry, café'

Stay with the string helpers, or continue below to break whole documents into tokens.

********************************
 Tokenizing your first document
********************************

Go from a string of HTML to a stream of tokens you can inspect.

Start with a small document and hand it to :func:`turbohtml.tokenize`, which returns an iterator of
:class:`turbohtml.Token` objects:

.. code-block:: pycon

    >>> import turbohtml
    >>> for token in turbohtml.tokenize('<p class="intro">Tom &amp; Jerry</p>'):
    ...     print(token)
    Token(START_TAG, tag='p')
    Token(TEXT, data='Tom & Jerry')
    Token(END_TAG, tag='p')

``type`` identifies each token as a :class:`turbohtml.TokenType`. Start and end tags carry the lowercased tag name and
the attributes, decoded:

.. code-block:: pycon

    >>> start, text, end = turbohtml.tokenize('<p class="intro">Tom &amp; Jerry</p>')
    >>> start.type
    <TokenType.START_TAG: 1>
    >>> start.tag
    'p'
    >>> start.attrs
    [('class', 'intro')]

Text arrives with character references resolved (the ``&amp;`` above came through as a plain ``&``). Content that the
HTML specification treats as raw, such as a script body, arrives as one text token without further interpretation:

.. code-block:: pycon

    >>> [token.data for token in turbohtml.tokenize("<script>if (a < b) run()</script>")
    ...  if token.type is turbohtml.TokenType.TEXT]
    ['if (a < b) run()']

When the document arrives in pieces (from a network stream, for example), create a :class:`turbohtml.Tokenizer` and feed
the pieces as they come. Each ``feed()`` returns the tokens that piece completed, and ``close()`` flushes whatever
remains:

.. code-block:: pycon

    >>> tokenizer = turbohtml.Tokenizer()
    >>> [token.tag for token in tokenizer.feed("<div><sp")]
    ['div']
    >>> [token.tag for token in tokenizer.feed("an>")]
    ['span']
    >>> list(tokenizer.close())
    []

The incomplete ``<sp`` stayed buffered until the rest of the tag arrived. That is the whole tokenizer API. Head to the
:doc:`how-to` guides for task-focused recipes, including porting an existing :class:`python:html.parser.HTMLParser`
subclass, or the :doc:`reference` for the exact signatures.

********************************
 Parsing a document into a tree
********************************

A token stream is flat. To see which element contains which, you need the *structure*: a tree. Go from a string of HTML
to a navigable tree of nodes.

Hand a whole document to :func:`turbohtml.parse`. It applies the full WHATWG tree-construction algorithm (the same one
browsers run, including the error recovery that inserts the missing ``html``, ``head`` and ``body``) and returns a
:class:`turbohtml.Document`:

.. code-block:: pycon

    >>> import turbohtml
    >>> doc = turbohtml.parse("<h1>Hello</h1><p>Tom &amp; <a href='/x'>Jerry</a></p>")
    >>> doc.root
    Element('html')

:meth:`~turbohtml.Node.find` returns the first descendant matching a tag (and any attributes you pass), or ``None``:

.. code-block:: pycon

    >>> doc.find("a")
    Element('a')
    >>> doc.find("a").attrs
    {'href': '/x'}

Every node exposes its text and its markup. :attr:`~turbohtml.Node.text` is the concatenated character data of the
subtree, with references decoded; :attr:`~turbohtml.Node.html` re-serializes the subtree:

.. code-block:: pycon

    >>> paragraph = doc.find("p")
    >>> paragraph.text
    'Tom & Jerry'
    >>> paragraph.html
    '<p>Tom &amp; <a href="/x">Jerry</a></p>'

turbohtml models text as real child nodes (the WHATWG DOM shape), so a paragraph's children are its text runs and its
elements interleaved, in order. A node is a sequence of its children: iterate it, take its length, index into it:

.. code-block:: pycon

    >>> list(paragraph)
    [Text('Tom & '), Element('a')]
    >>> len(paragraph)
    2
    >>> paragraph[1]
    Element('a')

From any node you can walk outward as well as inward: :attr:`~turbohtml.Node.parent`,
:attr:`~turbohtml.Node.next_sibling`, and the lazy :attr:`~turbohtml.Node.ancestors` and
:attr:`~turbohtml.Node.descendants` iterators:

.. code-block:: pycon

    >>> link = doc.find("a")
    >>> link.parent
    Element('p')
    >>> [node.tag for node in link.ancestors if isinstance(node, turbohtml.Element)]
    ['p', 'body', 'html']

Because the node types are a sealed hierarchy, structural pattern matching works: each subtype unpacks its defining
field:

.. code-block:: pycon

    >>> for node in paragraph:
    ...     match node:
    ...         case turbohtml.Element(tag):
    ...             print("element", tag)
    ...         case turbohtml.Text(data):
    ...             print("text", repr(data))
    ...
    text 'Tom & '
    element a

That is the whole traversal API. Head to the :doc:`how-to` guides for task-focused recipes, or the :doc:`reference` for
the exact signatures.
