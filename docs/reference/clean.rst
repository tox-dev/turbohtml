#######
 Clean
#######

.. module:: turbohtml.clean

Clean untrusted or raw HTML: sanitize it against an allowlist and rewrite bare URLs into links. Sanitizing is a
successor to ``bleach.clean`` -- build a :class:`Policy` (or take a preset), then sanitize; a non-overridable baseline
removes scripting elements, event-handler attributes, and ``javascript:`` URLs regardless of the policy. Linkifying is a
successor to `bleach.linkify <https://github.com/mozilla/bleach>`_ -- it finds URLs and email addresses and wraps them
in ``<a>`` links, HTML-aware so it never links inside an existing ``<a>``, a raw-text element, or a caller's
``skip_tags``.

.. autofunction:: sanitize

.. autoclass:: Sanitizer
    :members: sanitize

.. autoclass:: Policy
    :members: strict, basic, relaxed

.. autoclass:: OnDisallowed
    :members:

************
 Linkifying
************

A :class:`Linkify` configuration object carries the knobs: a callback receives each generated :class:`Link` and returns
it to keep the link or ``None`` to leave the text bare, ``process_existing`` runs the callbacks over ``<a>`` tags
already in the input (a callback reads ``Link.existing`` to tell the two apart), ``extra_tlds`` extends bare-domain
detection beyond the built-in IANA table, and ``schemes`` restricts which explicit-scheme URLs autolink.

.. autofunction:: linkify

.. autoclass:: Linkify
    :members:

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

***********
 Minifying
***********

:func:`minify` shrinks an HTML document in one call -- it parses the input and serializes it through the round-trip-safe
:class:`~turbohtml.Minify` layout, so the output reparses to the same tree and minifying is idempotent
(``minify(minify(x)) == minify(x)``). It replaces ``minify-html`` and ``htmlmin``. The four transforms (fold
insignificant whitespace, omit optional tags, unquote attributes, strip comments) default on; pass a
:class:`~turbohtml.Minify` to turn any off.

.. autofunction:: minify

The HTML minify layout emits ``<style>`` and ``<script>`` bodies verbatim; to also minify embedded CSS, run
:func:`minify_css` (below) over a ``<style>`` body yourself, which is what ``minify-html``'s ``minify_css`` did inline.
``minify-html``'s ``minify_js`` has no counterpart. The doctype is always normalized to ``<!doctype html>``
(``minify-html``'s ``minify_doctype`` is implicit), and HTML has no processing instructions to drop
(``remove_processing_instructions`` is moot under the WHATWG parser, which reads them as bogus comments).

******************
 CSS minification
******************

Minify CSS the value-safe way: every transform produces output that parses to the same cascade, so there is nothing to
configure. :func:`minify_css` takes a whole stylesheet; :func:`minify_css_inline` takes a bare declaration list, the
value of an HTML ``style`` attribute.

.. autofunction:: minify_css

.. autofunction:: minify_css_inline

turbohtml.migration.bleach
==========================

.. module:: turbohtml.migration.bleach

A drop-in for ``bleach.clean`` for projects migrating off bleach. It translates bleach's arguments onto a
:class:`~turbohtml.clean.Policy`; the safety baseline still applies, so an ``attributes`` callable cannot re-admit an
event handler or a ``javascript:`` URL.

.. autofunction:: clean
