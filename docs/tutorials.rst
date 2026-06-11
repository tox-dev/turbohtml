###########
 Tutorials
###########

*****************
 Getting started
*****************

This tutorial walks you from an empty environment to escaping and unescaping your first HTML.

Install turbohtml from PyPI:

.. code-block:: console

    $ pip install turbohtml

Open a Python prompt and escape some text for safe inclusion in an HTML page:

.. code-block:: pycon

    >>> import turbohtml
    >>> turbohtml.escape("5 > 3 & 2 < 4")
    '5 &gt; 3 &amp; 2 &lt; 4'

By default the quotation marks are escaped too, which is what you want inside an attribute value:

.. code-block:: pycon

    >>> turbohtml.escape("name=\"O'Brien\"")
    'name=&quot;O&#x27;Brien&quot;'

Now go the other way and turn HTML character references back into text:

.. code-block:: pycon

    >>> turbohtml.unescape("Tom &amp; Jerry &mdash; caf&eacute;")
    'Tom & Jerry — café'

From here you can stay with the string helpers, or continue below to break whole documents into tokens.

********************************
 Tokenizing your first document
********************************

This tutorial takes you from a string of HTML to a stream of tokens you can inspect.

Start with a small document and hand it to :func:`turbohtml.tokenize`, which returns an iterator of
:class:`turbohtml.Token` objects:

.. code-block:: pycon

    >>> import turbohtml
    >>> for token in turbohtml.tokenize('<p class="intro">Tom &amp; Jerry</p>'):
    ...     print(token)
    Token(START_TAG, tag='p')
    Token(TEXT, data='Tom & Jerry')
    Token(END_TAG, tag='p')

Each token tells you what it is through ``type``, a :class:`turbohtml.TokenType`. Start and end tags carry the
lowercased tag name and the attributes, already decoded:

.. code-block:: pycon

    >>> start, text, end = turbohtml.tokenize('<p class="intro">Tom &amp; Jerry</p>')
    >>> start.type
    <TokenType.START_TAG: 1>
    >>> start.tag
    'p'
    >>> start.attrs
    [('class', 'intro')]

Text arrives with character references already resolved — the ``&amp;`` above came through as a plain ``&`` — and
content that the HTML specification treats as raw, such as a script body, arrives as one text token without further
interpretation:

.. code-block:: pycon

    >>> [token.data for token in turbohtml.tokenize("<script>if (a < b) run()</script>")
    ...  if token.type is turbohtml.TokenType.TEXT]
    ['if (a < b) run()']

When the document arrives in pieces — from a network stream, for example — create a :class:`turbohtml.Tokenizer` and
feed the pieces as they come. Each ``feed()`` returns the tokens that piece completed, and ``close()`` flushes whatever
remains:

.. code-block:: pycon

    >>> tokenizer = turbohtml.Tokenizer()
    >>> [token.tag for token in tokenizer.feed("<div><sp")]
    ['div']
    >>> [token.tag for token in tokenizer.feed("an>")]
    ['span']
    >>> list(tokenizer.close())
    []

Notice the incomplete ``<sp`` stayed buffered until the rest of the tag arrived. That is the whole tokenizer API. From
here, head to the :doc:`how-to` guides for task-focused recipes — including porting an existing
:class:`python:html.parser.HTMLParser` subclass — or the :doc:`reference` for the exact signatures.
