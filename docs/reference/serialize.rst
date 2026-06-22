###########
 Serialize
###########

.. currentmodule:: turbohtml

Turn a tree back into markup or text. :meth:`Node.serialize` and :meth:`Node.encode` produce HTML; a :class:`Formatter`
picks the escape policy and an :class:`Indent` or :class:`Minify` picks the whitespace. :func:`escape` and
:func:`unescape` are the standalone string helpers. :meth:`Node.to_text`, :meth:`Node.to_markdown`, and
:meth:`Node.to_annotated_text` render text; :func:`annotation_surface` and :func:`annotation_tags` post-process the
annotated-text result.

.. autofunction:: escape

.. autofunction:: unescape

.. autoclass:: Formatter
    :members:

.. autoclass:: Indent
    :members:

.. autoclass:: Minify
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
