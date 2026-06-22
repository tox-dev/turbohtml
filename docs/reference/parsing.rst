#########
 Parsing
#########

.. currentmodule:: turbohtml

Turn markup into a navigable :class:`Document`. :func:`parse` handles a whole document at once; :func:`parse_fragment`
parses a fragment in a context element; :class:`IncrementalParser` builds a document from chunks fed over a stream.

.. autofunction:: parse

.. autofunction:: parse_fragment

.. autoclass:: IncrementalParser
    :members:

.. autoclass:: Document
    :members:

.. autoclass:: ParseError
    :members:

.. autoexception:: HTMLParseError

****************************
 turbohtml.migration.stdlib
****************************

.. module:: turbohtml.migration.stdlib

A drop-in base class for :class:`python:html.parser.HTMLParser` subclasses, over turbohtml's WHATWG-conformant
tokenizer. Subclass it, override the ``handle_*`` methods, and feed input incrementally as with the standard library.

.. autoclass:: HTMLParser
    :members:
