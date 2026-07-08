#########
 Parsing
#########

.. currentmodule:: turbohtml

Turn markup into a navigable :class:`Document`. :func:`parse` handles a whole document at once; :func:`parse_fragment`
parses a fragment in a context element; :class:`IncrementalParser` builds a document from chunks fed over a stream.
:func:`parse_xml` switches to strict XML 1.0 well-formedness for XML rather than HTML input.

Both :func:`parse` and :func:`parse_fragment` take keyword options: ``encoding`` and ``detect_encoding`` steer decoding
of ``bytes`` input, ``strict`` turns the first recovered parse error into an exception, ``positions`` records each
element's source line and column, ``source_locations`` additionally records the granular :class:`SourceLocation` spans
(read via :attr:`Node.source_location`), ``scripting`` sets the WHATWG scripting flag so ``<noscript>`` parses as a
raw-text element, and ``allow_declarative_shadow_roots`` honors a ``<template shadowrootmode>`` by attaching a shadow
root to its parent (on by default for :func:`parse`, off for :func:`parse_fragment`). Each is described on the function
below.

.. autofunction:: parse

.. autofunction:: parse_xml

.. autofunction:: parse_fragment

.. autoclass:: IncrementalParser
    :members:

.. autoclass:: Document
    :members:

.. autoclass:: ParseError
    :members:

.. autoexception:: HTMLParseError

*********************
 turbohtml.treebuild
*********************

.. module:: turbohtml.treebuild

Retarget the parser at a tree you control. :func:`parse_into` runs the same WHATWG tree builder and drives a builder
object -- one ``create_*`` per node kind plus an ``append`` -- to construct the tree directly, with no navigable
:class:`~turbohtml.Node` materialized and no second walk. It is Rust html5ever's ``TreeSink`` and Node parse5's
``TreeAdapter`` in turbohtml shape. See :doc:`/explanation/treebuild` for how it differs from the SAX stream and
:doc:`/how-to/treebuild` for worked builders.

.. autofunction:: parse_into

.. autoclass:: TreeBuilder
    :members:

****************************
 turbohtml.migration.stdlib
****************************

.. module:: turbohtml.migration.stdlib

A drop-in base class for :class:`python:html.parser.HTMLParser` subclasses, over turbohtml's WHATWG-conformant
tokenizer. Subclass it, override the ``handle_*`` methods, and feed input incrementally as with the standard library.

.. autoclass:: HTMLParser
    :members:
