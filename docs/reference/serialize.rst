###########
 Serialize
###########

.. currentmodule:: turbohtml

Pass ``inner=True`` to :meth:`Node.serialize`, :meth:`Node.encode`, or :meth:`Node.serialize_iter` to emit the context
node's children without its own tags. Existing calls retain outer serialization. The flag is keyword-only and works with
the existing ``Html`` configuration.

.. list-table:: Child serialization with ``inner=True``
    :header-rows: 1

    - - Method
      - Result
      - Supported layouts
    - - ``serialize``
      - ``str``
      - Compact, ``Indent``, ``Minify``
    - - ``encode``
      - ``bytes`` in the requested encoding
      - Compact, ``Indent``, ``Minify``
    - - ``serialize_iter``
      - Iterator of ``str`` chunks
      - Compact, ``Indent``

See :doc:`/how-to/transforming-trees` for child encoding and streaming recipes, and
:doc:`/explanation/tree-transformations` for the distinction between tree cleanup and output formatting.

.. testcode::

    from typing import Final

    from turbohtml import Html, Minify, parse_fragment

    paragraph: Final = parse_fragment("<p>  one <b>two</b>  </p>").children[0]
    print(repr(paragraph.serialize(Html(layout=Minify()), inner=True)))
    print(paragraph.encode(inner=True))

.. testoutput::

    ' one <b>two</b> '
    b'  one <b>two</b>  '

Inner output retains the context's raw-text and whitespace-preservation rules. For example, ``script`` text keeps its
HTML raw-text escaping behavior, and ``pre`` retains its child whitespace without adding a synthetic leading newline.
Indentation starts at depth zero. Template output includes its content; childless nodes emit empty output. Document
inner and outer output are equivalent. This operation does not mutate the tree.

``serialize_iter`` still rejects ``Minify`` layouts. For supported layouts, joining its chunks equals ``serialize`` with
the same options and scope; do not mutate a tree while its iterator is live. The ``inner_xml`` accessor retains its
well-formed XML handling. This addition does not change XML settings or minification semantics.

Turn a tree back into markup or text. Each renderer takes one configuration object: :meth:`Node.serialize` and
:meth:`Node.encode` produce HTML under an :class:`Html` config (a :class:`Formatter` picks the escape policy, an
:class:`Indent` or :class:`Minify` picks the whitespace, and ``xml=True`` switches to XML/XHTML syntax), and
:meth:`Node.serialize_iter` streams the same HTML in bounded ``str`` chunks for a large page (every layout but
:class:`Minify`, which needs the whole tree); :meth:`Node.to_markdown` takes a :class:`Markdown` config; and
:meth:`Node.to_text` and :meth:`Node.to_annotated_text` take a :class:`PlainText` config; and :meth:`Node.canonicalize`
produces Canonical XML (c14n) bytes under a :class:`Canonical` config. :meth:`Node.to_source` takes no config: it
losslessly re-emits the verbatim source of everything a mutation left untouched (parse with ``source_locations=True``),
the tree-based counterpart to :func:`turbohtml.rewrite.rewrite`. :func:`escape` and :func:`unescape` are the standalone
string helpers; :func:`annotation_surface` and :func:`annotation_tags` post-process the annotated-text result. A
:class:`~turbohtml.clean.JSMinify` passed to :class:`Minify` extends HTML minification into inline ``<script>`` content;
the standalone :func:`~turbohtml.clean.minify_js` lives with the other minifiers in :mod:`turbohtml.clean`.

Markup serialization, :attr:`Node.text`, :meth:`Node.to_markdown`, :meth:`Node.to_text`, and
:meth:`Node.to_annotated_text` walk the tree without recursion and impose no nesting limit. The layout renderers indent
nested lists and block quotes up to 20 nesting levels, counting two per list level and one per block quote. Deeper
content keeps the indentation of that depth, so output stays proportional to the input.

.. autofunction:: escape

.. autofunction:: unescape

.. autoclass:: Html
    :members:

.. autoclass:: Formatter
    :members:

.. autoclass:: Indent
    :members:

.. autoclass:: Minify
    :members:

.. autoclass:: Markdown
    :members:

.. autoclass:: PlainText
    :members:

.. autoclass:: Canonical
    :members:

.. autofunction:: annotation_surface

.. autofunction:: annotation_tags

********************************
 turbohtml.migration.markupsafe
********************************

.. module:: turbohtml.migration.markupsafe

A safe-string for composing HTML, a drop-in for `markupsafe <https://markupsafe.palletsprojects.com>`_'s public surface.
Import it in place of ``markupsafe``. ``Markup`` overrides every ``str`` method that returns text so the result stays a
``Markup``; the methods below are the turbohtml-specific ones, the rest mirror :class:`str`.

.. autofunction:: escape

.. autofunction:: escape_silent

.. autofunction:: soft_str

.. autoclass:: Markup
    :members: escape, format, join, striptags, unescape

.. autoclass:: EscapeFormatter
    :members: format_field
