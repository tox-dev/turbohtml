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

Minify CSS the value-safe way: every transform produces output that parses to the same cascade. :func:`minify_css` takes
a whole stylesheet; :func:`minify_css_inline` takes a bare declaration list, the value of an HTML ``style`` attribute.
Both are value-safe at any :class:`Baseline`; the optional :class:`CSSMinify` only bounds how new the output *syntax*
may be.

.. autofunction:: minify_css

.. autofunction:: minify_css_inline

.. autoclass:: Baseline
    :members:

.. autoclass:: CSSMinify
    :members:

Transformations
===============

Every transformation below preserves the computed value: the output parses to the same cascade as the input on any
conformant browser. Each links to the specification that establishes the equivalence.

Numbers and dimensions
----------------------

- Drop a leading ``+``, redundant leading and trailing zeros, and switch to ``e``-notation when it is shorter (`Syntax 3
  §4.3.3 <https://www.w3.org/TR/css-syntax-3/#consume-a-number>`__, `Values 4 §6.1
  <https://www.w3.org/TR/css-values-4/#numbers>`__).
- Lower-case a known unit and drop the unit on a zero ``<length>`` (``0px`` → ``0``); angle, time, frequency and other
  dimensions keep their unit, since a bare ``0`` is a ``<length>`` only (`Values 4 §5.2
  <https://www.w3.org/TR/css-values-4/#lengths>`__).
- Fold a ``calc()`` of constant, like-united operands with exact rational arithmetic; a non-combinable or
  non-terminating result is kept verbatim, and ``+``/``-`` without surrounding whitespace is left untouched (`Values 4
  §10 <https://www.w3.org/TR/css-values-4/#calc-func>`__).

Colors
------

- Swap a named color and its hex for whichever is shorter, and shorten ``#rrggbb`` → ``#rgb`` and ``#rrggbbaa`` →
  ``#rgba`` (`Color 4 §5.2 <https://www.w3.org/TR/css-color-4/#hex-notation>`__, `§6.1
  <https://www.w3.org/TR/css-color-4/#named-colors>`__).
- Fold an opaque ``rgb()``/``hsl()`` to hex only when every channel lands on an exact 8-bit value; a fractional channel
  is kept functional, since rounding it changes the color (`Color 4 §15
  <https://www.w3.org/TR/css-color-4/#rgb-functions>`__).
- Collapse ``transparent`` and ``rgba(0,0,0,0)`` to ``#0000``, drop an alpha of ``1``, and use the shorter ``rgb()``/
  ``hsl()`` alias of ``rgba()``/``hsla()`` (`Color 4 §4 <https://www.w3.org/TR/css-color-4/#color-syntax>`__).

Shorthands
----------

- Collapse a 1–4 value box shorthand when mirrored edges are equal, and merge the four physical longhands back into the
  shorthand (`Box 3 §6–7 <https://www.w3.org/TR/css-box-3/#margins>`__, `Cascade 5 §2.2
  <https://www.w3.org/TR/css-cascade-5/#shorthand>`__).
- Collapse the ``background``, ``background-position``, ``background-repeat`` (`Backgrounds 3 §3
  <https://www.w3.org/TR/css-backgrounds-3/#backgrounds>`__), ``flex`` (`Flexbox 1 §7.1.1
  <https://www.w3.org/TR/css-flexbox-1/#flex-property>`__) and ``font`` (`Fonts 4 §2.7
  <https://www.w3.org/TR/css-fonts-4/#font-prop>`__) shorthands to their shortest equivalent form.
- Merge ``flex-direction`` + ``flex-wrap`` into ``flex-flow``, and each Box Alignment axis pair into its ``place-``
  shorthand -- ``align-content`` + ``justify-content`` into ``place-content``, ``align-items`` + ``justify-items`` into
  ``place-items``, and ``align-self`` + ``justify-self`` into ``place-self`` (`Box Alignment 3
  <https://www.w3.org/TR/css-align-3/#place-content>`__). With ``Baseline.NEWLY_AVAILABLE`` also merge
  ``top``/``right``/``bottom``/``left`` into ``inset`` (`Logical Properties 1
  <https://www.w3.org/TR/css-logical-1/#inset-properties>`__), the two ``overflow`` longhands, and the flex ``gap``.

Structure and selectors
-----------------------

- Collapse insignificant whitespace and strip comments, keeping a ``/*! … */`` bang comment (`Syntax 3 §3.3
  <https://www.w3.org/TR/css-syntax-3/#input-preprocessing>`__).
- Drop a declaration an identically-keyed later one overrides, remove an empty rule, merge adjacent rules with the same
  selector or an identical body, and fuse consecutive ``@media`` blocks that share a prelude (`Cascade 5 §6.4.4
  <https://www.w3.org/TR/css-cascade-5/#cascade-order>`__).
- Lower-case type selectors, trim combinator whitespace, drop a redundant universal ``*`` before a subclass, write the
  four legacy pseudo-elements with one colon (``::before`` → ``:before``), and unquote an attribute value that is a
  valid identifier (`Selectors 4 §5–6 <https://www.w3.org/TR/selectors-4/#attribute-selectors>`__, `Pseudo-Elements 4 §8
  <https://www.w3.org/TR/css-pseudo-4/#css2-compat>`__); a custom-property name keeps its case (`Variables 1 §2
  <https://www.w3.org/TR/css-variables-1/#defining-variables>`__).
- Rewrite a ``@keyframes`` ``from`` selector to ``0%`` (`Animations 1
  <https://www.w3.org/TR/css-animations-1/#keyframes>`__), and drop the space before ``and``/``or`` after a ``)`` in a
  media query (`Media Queries 4 <https://www.w3.org/TR/mediaqueries-4/#mq-syntax>`__).

turbohtml.migration.bleach
==========================

.. module:: turbohtml.migration.bleach

A drop-in for ``bleach.clean`` for projects migrating off bleach. It translates bleach's arguments onto a
:class:`~turbohtml.clean.Policy`; the safety baseline still applies, so an ``attributes`` callable cannot re-admit an
event handler or a ``javascript:`` URL.

.. autofunction:: clean
