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

:func:`sanitize_report` sanitizes and also returns what the policy dropped, one :class:`Removed` record per removed
element or stripped attribute, the way DOMPurify populates ``DOMPurify.removed``.

.. autofunction:: sanitize_report

.. autoclass:: Removed

.. autoclass:: Sanitizer
    :members: sanitize, sanitize_report

.. autoclass:: Policy
    :members: strict, basic, relaxed

``Policy.css_properties`` allowlists style property *names*; ``Policy.allowed_styles`` narrows further by *value*, the
way sanitize-html's ``allowedStyles`` does. Key it ``{tag: {property: [pattern, ...]}}`` (``"*"`` matches every tag); a
``style`` declaration survives only when its property is listed for the element's tag or ``"*"`` and its value matches
one of the patterns via an unanchored :func:`re.search`. It runs on top of ``css_properties`` and the dangerous-value
baseline -- the property must still be in ``css_properties``, and ``expression()`` or a disallowed-scheme ``url()`` is
dropped even when a pattern would admit it:

.. testcode::

    from turbohtml.clean import sanitize, Policy

    policy = Policy(
        tags=frozenset({"p"}),
        attributes={"p": frozenset({"style"})},
        css_properties=frozenset({"color"}),
        allowed_styles={"*": {"color": [r"^#[0-9a-f]{3,6}$"]}},
    )
    print(sanitize('<p style="color: #0a0; color: red">ok</p>', policy))

.. testoutput::

    <p style="color: #0a0">ok</p>

``Policy.transform_tags`` renames elements during the same walk, sanitize-html's ``transformTags``. Key it by source
tag: map to a bare string to rename, or to a :class:`Transform` to rename and add attributes. The rename runs *before*
the allowlist, so the renamed element is re-checked from scratch -- a transform decides an element's name but never its
safety. Mapping a tag to ``script`` still drops it, and an added attribute is scrubbed like the element's own, so it
must be allowlisted to survive:

.. testcode::

    from turbohtml.clean import sanitize, Policy, Transform

    policy = Policy(
        tags=frozenset({"strong", "div"}),
        attributes={"div": frozenset({"class"})},
        transform_tags={"b": "strong", "center": Transform("div", {"class": "center"})},
    )
    print(sanitize("<b>bold</b> and <center>middle</center>", policy))

.. testoutput::

    <strong>bold</strong> and <div class="center">middle</div>

``Policy.isolate_named_props`` prefixes every kept ``id`` and ``name`` value with ``user-content-``, DOMPurify's
``SANITIZE_NAMED_PROPS``. It stops DOM clobbering -- an ``id`` or ``name`` whose value matches a built-in ``document``
or form property shadows that property through named access -- by moving the value out of the property namespace. An
already-prefixed value is left alone, so re-sanitizing is a fixpoint. The isolation is applied after
``attribute_filter``, so the value a filter returns is the one that gets namespaced:

.. testcode::

    from turbohtml.clean import sanitize, Policy

    policy = Policy(
        tags=frozenset({"input"}),
        attributes={"input": frozenset({"name"})},
        isolate_named_props=True,
    )
    print(sanitize('<input name="attributes">', policy))

.. testoutput::

    <input name="user-content-attributes">

.. autoclass:: Transform

.. autoclass:: OnDisallowed
    :members:

The sanitizer ships bleach's default allowlists as module constants, so a :class:`Policy` can extend a known baseline
instead of enumerating a safe set from scratch.

.. autodata:: DEFAULT_TAGS
    :no-value:

    The tags the default policy keeps: ``a``, ``abbr``, ``acronym``, ``b``, ``blockquote``, ``code``, ``em``, ``i``,
    ``li``, ``ol``, ``strong``, ``ul``.

.. autodata:: DEFAULT_ATTRIBUTES
    :no-value:

    The attributes the default policy keeps, keyed by tag: ``href`` and ``title`` on ``a``, and ``title`` on ``abbr``
    and ``acronym``.

.. autodata:: DEFAULT_SCHEMES
    :no-value:

    The URL schemes the default policy allows in an ``href`` or ``src``: ``http``, ``https``, ``mailto``.

.. autodata:: DEFAULT_CSS_PROPERTIES
    :no-value:

    The CSS properties the default policy keeps when scrubbing a ``style`` attribute: the CSS 2.1 safe set plus the SVG
    paint properties.

************
 Linkifying
************

A :class:`Linkify` configuration object carries the knobs: a callback receives each generated :class:`LinkCandidate` and
returns it to keep the link or ``None`` to leave the text bare, ``process_existing`` runs the callbacks over ``<a>``
tags already in the input (a callback reads ``LinkCandidate.existing`` to tell the two apart), ``extra_tlds`` extends
bare-domain detection beyond the built-in IANA table, and ``schemes`` sets which explicit-scheme URLs autolink
(defaulting to the built-in ``http``/``https``/``ftp`` set, so a typo scheme or a ``javascript://`` payload stays plain
text).

.. autofunction:: linkify

.. autoclass:: Linkify
    :members:

.. autoclass:: Linker
    :members: linkify

.. autoclass:: LinkCandidate

.. autofunction:: nofollow

.. autofunction:: target_blank

.. autodata:: Callback
    :no-value:

    The type of a linkify callback: a callable that takes one :class:`LinkCandidate` and returns it to keep the link or
    ``None`` to leave the text bare. :func:`nofollow` and :func:`target_blank` are built-in examples.

.. autodata:: DEFAULT_CALLBACKS
    :no-value:

    The callbacks :func:`linkify` applies when the caller passes none: ``(nofollow,)``, so bare-URL links get
    ``rel="nofollow"`` unless you opt out.

To only *locate* links in plain text rather than rewrite HTML, use :class:`LinkDetector`. It returns a :class:`LinkSpan`
for each match and accepts custom ``tlds`` and scheme-less ``schemes``.

.. autoclass:: LinkDetector
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

The HTML minify layout emits ``<style>`` bodies verbatim; to also minify embedded CSS, run :func:`minify_css` (below)
over a ``<style>`` body yourself, which is what ``minify-html``'s ``minify_css`` did inline. Passing a :class:`JSMinify`
as ``Minify(minify_js=...)`` rewrites inline ``<script>`` content, covering ``minify-html``'s ``minify_js``. The doctype
is always normalized to ``<!doctype html>`` (``minify-html``'s ``minify_doctype`` is implicit), and HTML has no
processing instructions to drop (``remove_processing_instructions`` is moot under the WHATWG parser, which reads them as
bogus comments).

*****************
 JS minification
*****************

:func:`minify_js` minifies a JavaScript string on its own, the ``jsmin``/``rjsmin`` successor. It always folds
whitespace, comments, and number literals; a frozen :class:`JSMinify` toggles the optional ``mangle`` (rename local
bindings) and ``fold`` (constant-fold and eliminate dead code) passes. The same :class:`JSMinify` passed as
``Minify(minify_js=...)`` rewrites inline ``<script>`` content during HTML minification.

.. autofunction:: minify_js

.. autoclass:: JSMinify
    :members:

******************
 CSS minification
******************

Minify CSS the value-safe way: every transform produces output that parses to the same cascade. :func:`minify_css` takes
a whole stylesheet; :func:`minify_css_inline` takes a bare declaration list, the value of an HTML ``style`` attribute.
Both are value-safe at any baseline; the optional :class:`CSSMinify` ``baseline`` year only bounds how new the output
*syntax* may be.

.. autofunction:: minify_css

.. autofunction:: minify_css_inline

.. autoclass:: CSSMinify

Transformations
===============

Every transformation below preserves the computed value: the output parses to the same cascade as the input on any
conformant browser. Each links to the specification that establishes the equivalence.

Numbers and dimensions
----------------------

- Drop a leading ``+``, redundant leading and trailing zeros, and switch to ``e``-notation when it is shorter (`Syntax 3
  §4.3.3 <https://www.w3.org/TR/css-syntax-3/#consume-a-number>`__, `Values 4 §6.1
  <https://www.w3.org/TR/css-values-4/#numbers>`__). ``a{width:+0.50px}`` → ``a{width:.5px}``, ``a{margin:100000px}`` →
  ``a{margin:1e5px}``.
- Lower-case a known unit and drop the unit on a zero ``<length>`` (``0px`` → ``0``); angle, time, frequency and other
  dimensions keep their unit, since a bare ``0`` is a ``<length>`` only (`Values 4 §5.2
  <https://www.w3.org/TR/css-values-4/#lengths>`__). ``a{margin:0PX}`` → ``a{margin:0}``, while
  ``a{transform:rotate(0deg)}`` is unchanged.
- Fold a ``calc()`` of constant, like-united operands with exact rational arithmetic; a non-combinable or
  non-terminating result is kept verbatim, and ``+``/``-`` without surrounding whitespace is left untouched (`Values 4
  §10 <https://www.w3.org/TR/css-values-4/#calc-func>`__). ``a{width:calc(1px + 2px)}`` → ``a{width:3px}``.

Colors
------

- Swap a named color and its hex for whichever is shorter, and shorten ``#rrggbb`` → ``#rgb`` and ``#rrggbbaa`` →
  ``#rgba`` (`Color 4 §5.2 <https://www.w3.org/TR/css-color-4/#hex-notation>`__, `§6.1
  <https://www.w3.org/TR/css-color-4/#named-colors>`__). ``a{color:#ffffff}`` → ``a{color:#fff}``, ``a{color:#000080}``
  → ``a{color:navy}``.
- Fold an opaque ``rgb()``/``hsl()`` to hex only when every channel lands on an exact 8-bit value; a fractional channel
  is kept functional, since rounding it changes the color (`Color 4 §15
  <https://www.w3.org/TR/css-color-4/#rgb-functions>`__). ``a{color:rgb(255,0,0)}`` → ``a{color:red}``.
- Collapse ``transparent`` and ``rgba(0,0,0,0)`` to ``#0000``, drop an alpha of ``1``, and use the shorter ``rgb()``/
  ``hsl()`` alias of ``rgba()``/``hsla()`` (`Color 4 §4 <https://www.w3.org/TR/css-color-4/#color-syntax>`__).
  ``a{color:transparent}`` → ``a{color:#0000}``, ``a{color:rgba(1,2,3,.5)}`` → ``a{color:rgb(1,2,3,.5)}``.

Shorthands
----------

- Collapse a 1–4 value box shorthand when mirrored edges are equal, and merge the four physical longhands back into the
  shorthand (`Box 3 §6–7 <https://www.w3.org/TR/css-box-3/#margins>`__, `Cascade 5 §2.2
  <https://www.w3.org/TR/css-cascade-5/#shorthand>`__). ``a{margin:1px 1px 1px 1px}`` → ``a{margin:1px}``.
- Collapse the ``background``, ``background-position``, ``background-repeat`` (`Backgrounds 3 §3
  <https://www.w3.org/TR/css-backgrounds-3/#backgrounds>`__), ``flex`` (`Flexbox 1 §7.1.1
  <https://www.w3.org/TR/css-flexbox-1/#flex-property>`__) and ``font`` (`Fonts 4 §2.7
  <https://www.w3.org/TR/css-fonts-4/#font-prop>`__) shorthands to their shortest equivalent form. ``a{font:bold 12px
  x}`` → ``a{font:700 12px x}``.
- Merge ``flex-direction`` + ``flex-wrap`` into ``flex-flow``, and each Box Alignment axis pair into its ``place-``
  shorthand -- ``align-content`` + ``justify-content`` into ``place-content``, ``align-items`` + ``justify-items`` into
  ``place-items``, and ``align-self`` + ``justify-self`` into ``place-self`` (`Box Alignment 3
  <https://www.w3.org/TR/css-align-3/#place-content>`__). ``a{align-items:center;justify-items:center}`` →
  ``a{place-items:center}``.
- Merge the four ``border-*-radius`` corners into ``border-radius`` (`Backgrounds 3 §6
  <https://www.w3.org/TR/css-backgrounds-3/#border-radius>`__) and ``outline-width`` + ``outline-style`` +
  ``outline-color`` into ``outline`` (`UI 4 §3.1 <https://www.w3.org/TR/css-ui-4/#outline>`__); each resets only its own
  longhands, and the corner merge stands down for an elliptical corner or a logical sibling. ``a{outline-width:1px;
  outline-style:solid;outline-color:red}`` → ``a{outline:1px solid red}``.

Structure and selectors
-----------------------

- Collapse insignificant whitespace and strip comments, keeping a ``/*! … */`` bang comment (`Syntax 3 §3.3
  <https://www.w3.org/TR/css-syntax-3/#input-preprocessing>`__). ``/*c*/a{color:red}`` → ``a{color:red}``, while ``/*!
  keep */a{color:red}`` → ``/*!keep*/a{color:red}``.
- Drop a declaration an identically-keyed later one overrides, remove an empty rule or an empty ``@media``/
  ``@supports``/``@container`` (an empty ``@layer`` or ``@keyframes`` is kept, since either is observable), merge two
  rules with the same selector or an identical body -- even across intervening rules, as long as each sets none of the
  moved properties so the cascade cannot change -- and fuse consecutive ``@media`` blocks that share a prelude (`Cascade
  5 §6.4.4 <https://www.w3.org/TR/css-cascade-5/#cascade-order>`__). ``a{}b{c:d}`` → ``b{c:d}``, ``@media print{}`` is
  dropped, and ``a{color:red}b{margin:0}a{font-size:2px}`` → ``a{color:red;font-size:2px}b{margin:0}``.
- Lower-case type selectors, trim combinator whitespace, drop a redundant universal ``*`` before a subclass, write the
  four legacy pseudo-elements with one colon (``::before`` → ``:before``), and unquote an attribute value that is a
  valid identifier (`Selectors 4 §5–6 <https://www.w3.org/TR/selectors-4/#attribute-selectors>`__, `Pseudo-Elements 4 §8
  <https://www.w3.org/TR/css-pseudo-4/#css2-compat>`__); a custom-property name keeps its case (`Variables 1 §2
  <https://www.w3.org/TR/css-variables-1/#defining-variables>`__).
- Keep a custom-property value byte-exact past edge trimming: §2 defines the value as the literal token stream,
  ``var()`` splices it verbatim, and ``getComputedStyle().getPropertyValue()`` reads it back as written, so collapsing
  its internal whitespace -- which the other PyPI minifiers do for a few hundred bytes on custom-property-heavy CSS --
  is observable and never applied. ``A{X:1}`` → ``a{x:1}``, ``[a="Foo"]{x:1}`` → ``[a=Foo]{x:1}``.
- Rewrite a ``@keyframes`` ``from`` selector to ``0%`` (`Animations 1
  <https://www.w3.org/TR/css-animations-1/#keyframes>`__), and drop the space before ``and``/``or`` after a ``)`` in a
  media query (`Media Queries 4 <https://www.w3.org/TR/mediaqueries-4/#mq-syntax>`__). ``@keyframes k{from{opacity:0}}``
  → ``@keyframes k{0%{opacity:0}}``.

Baseline
--------

Every transform above is value-safe and, by default, applies to every stylesheet -- its output syntax has been
interoperable for years. A few emit a shorthand whose interop is more recent, so they are gated on the
:class:`CSSMinify` ``baseline`` year and stay off unless you target that year or later. Each row lists the year a
transform's output syntax reached `Baseline <https://web.dev/baseline>`__; a transform tagged year ``Y`` runs when
``baseline >= Y``.

.. list-table::
    :header-rows: 1
    :widths: 18 82

    - - Baseline year
      - Transforms enabled
    - - any (``None``, default)
      - Every transform in the sections above: numbers, colors, box/flex/``place-`` shorthands, structure, selectors.
    - - ``2021``
      - Merge ``top``/``right``/``bottom``/``left`` into ``inset``, merge the two ``overflow`` longhands into
        ``overflow``, merge ``row-gap`` + ``column-gap`` into the flex ``gap``, and merge each logical inline/block pair
        into its shorthand -- ``margin-inline``, ``margin-block``, ``padding-inline``, ``padding-block``,
        ``inset-inline``, ``inset-block`` (`Logical Properties 1 <https://www.w3.org/TR/css-logical-1/>`__); a logical
        merge stands down when the physical longhand it aliases is in the rule. At ``baseline=2021``,
        ``a{top:0;right:0;bottom:0;left:0}`` → ``a{inset:0}`` and ``a{margin-inline-start:1px;margin-inline-end:2px}`` →
        ``a{margin-inline:1px 2px}``.

turbohtml.migration.bleach
==========================

.. module:: turbohtml.migration.bleach

A drop-in for ``bleach.clean`` for projects migrating off bleach. It translates bleach's arguments onto a
:class:`~turbohtml.clean.Policy`; the safety baseline still applies, so an ``attributes`` callable cannot re-admit an
event handler or a ``javascript:`` URL.

.. autofunction:: clean
