###########
 Serialize
###########

.. currentmodule:: turbohtml

Turn a tree back into markup or text. Each renderer takes one configuration object: :meth:`Node.serialize` and
:meth:`Node.encode` produce HTML under an :class:`Html` config (a :class:`Formatter` picks the escape policy and an
:class:`Indent` or :class:`Minify` picks the whitespace), and :meth:`Node.serialize_iter` streams the same HTML in
bounded ``str`` chunks for a large page (every layout but :class:`Minify`, which needs the whole tree);
:meth:`Node.to_markdown` takes a :class:`Markdown` config; and :meth:`Node.to_text` and :meth:`Node.to_annotated_text`
take a :class:`PlainText` config. :func:`escape` and :func:`unescape` are the standalone string helpers;
:func:`annotation_surface` and :func:`annotation_tags` post-process the annotated-text result. A
:class:`~turbohtml.clean.JSMinify` passed to :class:`Minify` extends HTML minification into inline ``<script>`` content;
the standalone :func:`~turbohtml.clean.minify_js` lives with the other minifiers in :mod:`turbohtml.clean`.

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

.. autofunction:: annotation_surface

.. autofunction:: annotation_tags

*****************
 turbohtml.build
*****************

.. module:: turbohtml.build

A terse builder for constructing HTML trees, in the spirit of ``lxml.builder.E``. It is a thin layer over
:class:`~turbohtml.Element` and :meth:`~turbohtml.Node.serialize`: ``E.<tag>(attrs, *children)`` builds a real element,
folds a leading mapping into its attributes, and appends each remaining argument as a child (a string becomes a
:class:`~turbohtml.Text` node, a node is appended as-is).

.. autodata:: E
    :no-value:

.. autoclass:: ElementMaker
    :members:

.. autofunction:: document

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
