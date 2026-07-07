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

The incomplete ``<sp`` stayed buffered until the rest of the tag arrived.

The tokenizer tracks the offset of every construct as it runs, and :func:`turbohtml.parse` can carry those offsets onto
the tree it builds. Pass ``source_locations=True`` and each element's :attr:`~turbohtml.Node.source_location` gives the
span of its start tag, its end tag, and each attribute -- the same information as parse5's ``sourceCodeLocationInfo``,
here as :class:`~turbohtml.SourceSpan` records whose offsets slice the original source:

.. testcode::

    source = '<p class="intro">hi</p>'
    element = turbohtml.parse(source, source_locations=True).find("p")
    span = element.source_location.attrs["class"]
    print(source[span.start_offset : span.end_offset])

.. testoutput::

    class="intro"

The tokenizer follows the HTML rules throughout: it lowercases tag names, treats ``<script>`` as raw text, and recovers
from malformed markup the way a browser does. When the input is XML rather than HTML, reach for
:func:`turbohtml.parse_xml` instead, which parses under XML 1.0 well-formedness -- names stay case-sensitive, ``<x/>``
self-closes any element, and a CDATA section becomes its own node:

.. testcode::

    doc = turbohtml.parse_xml("<Note><Body/>Buy <![CDATA[<milk>]]></Note>")
    root = doc.children[0]
    print(root.tag)
    print([child.tag for child in root if isinstance(child, turbohtml.Element)])
    print(root.children[-1].data)

.. testoutput::

    Note
    ['Body']
    <milk>

A mismatched or unclosed tag is a well-formedness error there, not something to recover from -- it raises
:exc:`~turbohtml.HTMLParseError` instead of building a repaired tree.

Tokens are the raw stream; they know nothing about tree structure. When you want the events a browser's parser would
fire -- with the implied ``<html>``, ``<head>``, and ``<body>`` filled in and stray table content foster-parented into
place -- but you do not want to hold a tree, reach for :mod:`turbohtml.saxparse`.
:func:`~turbohtml.saxparse.iter_events` parses a document and yields typed events in document order, materializing one
at a time and keeping no tree:

.. testcode::

    from turbohtml.saxparse import iter_events

    for event in iter_events("<table>stray<tr><td>cell"):
        print(event)

.. testoutput::

    StartElement(tag='html', attrs=())
    StartElement(tag='head', attrs=())
    EndElement(tag='head')
    StartElement(tag='body', attrs=())
    Characters(data='stray')
    StartElement(tag='table', attrs=())
    StartElement(tag='tbody', attrs=())
    StartElement(tag='tr', attrs=())
    StartElement(tag='td', attrs=())
    Characters(data='cell')
    EndElement(tag='td')
    EndElement(tag='tr')
    EndElement(tag='tbody')
    EndElement(tag='table')
    EndElement(tag='body')
    EndElement(tag='html')

The ``<tbody>`` no one wrote is there, and ``stray`` was moved out ahead of the ``<table>`` -- the tree the parser
builds, delivered as events rather than nodes. If you prefer callbacks over a loop, subclass
:class:`~turbohtml.saxparse.SaxHandler` and pass it to :func:`~turbohtml.saxparse.sax_parse`.

The tree builder does more than fill in implied elements while it consumes the token stream. When it meets a
``<template>`` carrying a ``shadowrootmode``, it attaches a *declarative shadow root* to the template's parent and
parses the template's content into that shadow tree instead of a template content fragment -- the same markup a browser
turns into a shadow root. The template element itself never lands in the light tree, so the parent serializes without
it:

.. testcode::

    from turbohtml import parse

    card = parse("<div id=card><template shadowrootmode=open><slot></slot></template><p>Body</p></div>").find(id="card")
    print(card.shadow_root.mode)
    print(card.html)

.. testoutput::

    open
    <div id="card"><p>Body</p></div>

Declarative shadow roots are honored for whole-document :func:`~turbohtml.parse` by default; pass
``allow_declarative_shadow_roots=False`` to keep such templates as ordinary elements. See :doc:`/how-to/shadow-dom` for
working with the shadow tree the parser built.

That is the whole tokenizer API. If you are porting an existing :class:`python:html.parser.HTMLParser` subclass,
:class:`turbohtml.migration.stdlib.HTMLParser` keeps the same ``handle_*`` callbacks over this tokenizer, so the
migration is changing the base class. Head to the :doc:`/how-to/index` guides for task-focused recipes or the
:doc:`/reference` for the exact signatures.
