#######################
 Tokenizing a document
#######################

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

    print([
        token.data
        for token in turbohtml.tokenize("<script>if (a < b) run()</script>")
        if token.type is turbohtml.TokenType.TEXT
    ])

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
:doc:`/how-to/index` guides for task-focused recipes or the :doc:`/reference` for the exact signatures.
