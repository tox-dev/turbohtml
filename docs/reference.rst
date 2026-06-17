###########
 Reference
###########

.. automodule:: turbohtml
    :members:
    :exclude-members: TokenType
    :special-members: __version__

.. autoclass:: turbohtml.TokenType
    :members:
    :undoc-members:

******************
 turbohtml.markup
******************

.. module:: turbohtml.markup

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

*******************
 turbohtml.linkify
*******************

.. module:: turbohtml.linkify

Find URLs and email addresses in HTML and wrap them in ``<a>`` links, a successor to `bleach.linkify
<https://github.com/mozilla/bleach>`_. It is HTML-aware, so it never links inside an existing ``<a>``, a raw-text
element, or a caller's ``skip_tags``. A callback receives each generated :class:`Link` and returns it to keep the link
or ``None`` to leave the text bare.

.. autofunction:: linkify

.. autoclass:: Linker
    :members: linkify

.. autoclass:: Link

.. autofunction:: nofollow

.. autofunction:: target_blank
