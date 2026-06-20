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
or ``None`` to leave the text bare. ``process_existing`` runs the callbacks over ``<a>`` tags already in the input (a
callback reads ``Link.existing`` to tell the two apart), ``extra_tlds`` extends bare-domain detection beyond the
built-in IANA table, and ``schemes`` restricts which explicit-scheme URLs autolink.

.. autofunction:: linkify

.. autoclass:: Linker
    :members: linkify

.. autoclass:: Link

.. autofunction:: nofollow

.. autofunction:: target_blank

To only *locate* links in plain text rather than rewrite HTML, use :class:`Detector`. It returns a :class:`LinkSpan` for
each match and accepts custom ``tlds`` and scheme-less ``schemes``.

.. autoclass:: Detector
    :members: find, has_link

.. autoclass:: LinkSpan
    :members:

*********************
 turbohtml.sanitizer
*********************

.. module:: turbohtml.sanitizer

Sanitize untrusted HTML against an allowlist, a successor to ``bleach.clean``. Build a :class:`Policy` (or take a
preset), then sanitize. A non-overridable baseline removes scripting elements, event-handler attributes, and
``javascript:`` URLs regardless of the policy.

.. autofunction:: sanitize

.. autoclass:: Sanitizer
    :members: sanitize

.. autoclass:: Policy
    :members: strict, basic, relaxed

.. autoclass:: OnDisallowed
    :members:

*************************
 turbohtml.bleach_compat
*************************

.. module:: turbohtml.bleach_compat

A drop-in for ``bleach.clean`` for projects migrating off bleach. It translates bleach's arguments onto a
:class:`~turbohtml.sanitizer.Policy`; the safety baseline still applies, so an ``attributes`` callable cannot re-admit
an event handler or a ``javascript:`` URL.

.. autofunction:: clean

***********************
 turbohtml.html_parser
***********************

.. module:: turbohtml.html_parser

A drop-in base class for :class:`python:html.parser.HTMLParser` subclasses, over turbohtml's WHATWG-conformant
tokenizer. Subclass it, override the ``handle_*`` methods, and feed input incrementally as with the standard library.

.. autoclass:: HTMLParser
    :members:

*****************
 turbohtml.query
*****************

.. module:: turbohtml.query

A pyquery-style fluent, chainable query wrapper over the tree and selector engine, for code migrating off pyquery's
jQuery-style chaining. Each traversal and mutation method returns a :class:`Query`, so calls compose.

.. autoclass:: Query
    :members:
    :special-members: __call__
