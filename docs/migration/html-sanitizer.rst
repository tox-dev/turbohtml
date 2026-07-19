#####################
 From html-sanitizer
#####################

.. package-meta:: html-sanitizer matthiask/html-sanitizer

`html-sanitizer <https://github.com/matthiask/html-sanitizer>`_ is an allowlist HTML sanitizer built on lxml, from the
author of FeinCMS and django-content-editor. You configure it with a ``settings`` dict: ``tags`` (a set of allowed
elements), ``attributes`` (allowed attribute names keyed by tag), ``empty`` and ``separate`` (tags that may stay empty
or must not merge with an adjacent twin), a ``sanitize_href`` scheme check, ``add_nofollow``, ``autolink``, and
``element_preprocessors``/``element_postprocessors`` hooks. Beyond dropping disallowed markup it normalizes the tree:
collapsing whitespace, merging adjacent identical tags, and dropping empty ones. It is the sanitizer behind Django CMS
and rich-text-editor stacks that store user-authored HTML.

turbohtml covers the same allowlist job from its ``turbohtml.clean`` module. The move is a
settings-to-:class:`~turbohtml.clean.Policy` translation rather than a rethink, and turbohtml runs the filtering in C
over its own WHATWG tree builder instead of an lxml parse.

*****************************
 turbohtml vs html-sanitizer
*****************************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - html-sanitizer
    - - Scope
      - Full WHATWG parser, serializer, sanitizer, linkifier, minifier, selectors
      - Allowlist sanitize with whitespace and typographic normalization, over lxml
    - - Feature breadth
      - Escape/strip/remove per tag, value-rewriting attribute filter, forced attributes, CSS-property scrubbing, fixed
        safety baseline
      - Tag/attribute allowlist, tag merging, whitespace and typographic cleanup, autolink, pre/post-processor hooks
    - - Performance
      - Filtering in C on a native tree
      - Pure Python over an lxml parse
    - - Typing
      - Fully annotated, ``py.typed``
      - Untyped
    - - Dependencies
      - None (self-contained C extension)
      - lxml
    - - Maintenance
      - Active
      - Active, single maintainer

Feature overlap
===============

The shared allowlist surface ports one-to-one:

- ``Sanitizer(settings).sanitize(text)`` -> :func:`turbohtml.clean.sanitize` with a :class:`~turbohtml.clean.Policy`, or
  a reusable :class:`~turbohtml.clean.Sanitizer`.
- ``settings["tags"]`` (a set) -> ``Policy.tags`` (a ``frozenset``).
- ``settings["attributes"]`` (per-tag dict) -> ``Policy.attributes`` (per-tag ``frozenset``; ``"*"`` as key matches
  every tag).
- ``sanitize_href`` scheme check -> ``Policy.url_schemes`` plus ``Policy.allow_relative_urls``.
- ``add_nofollow=True`` -> ``Policy.add_link_rel = frozenset({"nofollow"})``.

What turbohtml adds
===================

- A frozen, thread-safe :class:`~turbohtml.clean.Policy` you build once and reuse across threads.
- An :class:`~turbohtml.clean.OnDisallowed` enum that names three outcomes for a disallowed tag: ``ESCAPE`` (render as
  text), ``STRIP`` (unwrap, keep children), and ``REMOVE`` (drop the subtree). html-sanitizer only unwraps.
- ``Policy.remove_with_content`` and a fixed safety baseline that drops ``<script>``, ``on*`` event handlers, and
  ``javascript:`` URLs by construction, so no allowlist can re-admit them and script text never leaks into the output.
- ``Policy.css_properties``: when ``style`` is allowed, its declarations are scrubbed against a safe property set.
- ``Policy.allowed_styles``: a per-element, per-property value allowlist for the ``style`` attribute, keyed ``{tag:
  {property: [pattern, ...]}}`` with ``"*"`` matching every tag. It ports sanitize-html's ``allowedStyles`` -- a
  declaration survives only when its value matches one of the property's patterns -- narrowing ``css_properties`` by
  value without weakening the dangerous-value baseline.
- ``Policy.attribute_filter`` to rewrite or drop any surviving attribute value, and ``Policy.set_attributes`` to force
  attribute values onto every kept instance of a tag.
- ``Policy.add_link_rel`` generalizes ``add_nofollow`` to any ``rel`` token set (for example ``noopener``,
  ``noreferrer``).
- Ready-made :meth:`Policy.strict() <turbohtml.clean.Policy.strict>`, :meth:`~turbohtml.clean.Policy.basic`, and
  :meth:`~turbohtml.clean.Policy.relaxed` presets.

What html-sanitizer has that turbohtml does not
===============================================

- Whitespace collapsing, adjacent-tag merging, and empty-tag dropping (the ``whitespace``, ``separate``, and ``empty``
  settings) run as part of every ``sanitize`` call. turbohtml has no direct port; do the normalization with a walk over
  the returned tree.
- ``keep_typographic`` rewrites quotes and dashes to typographic characters. No equivalent.
- ``element_preprocessors`` and ``element_postprocessors`` insert arbitrary structural transforms into the pass.
  turbohtml's ``attribute_filter`` covers value-level rewriting, but structural post-processing is left to a walk over
  the tree.
- ``sanitize_href`` is an arbitrary callable, so it can rewrite or reject a URL on any rule. turbohtml checks a scheme
  allowlist (``url_schemes`` plus ``allow_relative_urls``); custom per-URL logic goes through ``attribute_filter``
  instead.
- ``autolink`` fuses URL detection into the sanitize call. turbohtml linkifies with a separate
  :func:`turbohtml.clean.linkify` pass rather than a sanitizer setting.

Performance
===========

turbohtml leads html-sanitizer by 30 to 41 times, the WHATWG tree builder in C standing in for the lxml parse:

.. bench-table::
    :file: bench/html-sanitizer.json

****************
 How to migrate
****************

Swap the import and translate the ``settings`` dict into a :class:`~turbohtml.clean.Policy`:

.. code-block:: python

    # html-sanitizer
    from html_sanitizer import Sanitizer

    Sanitizer({"tags": {"a", "p"}, "attributes": {"a": {"href"}}, "add_nofollow": True}).sanitize(text)

    # turbohtml
    from turbohtml.clean import sanitize, Policy

    sanitize(
        text,
        Policy(
            tags=frozenset({"a", "p"}),
            attributes={"a": frozenset({"href"})},
            add_link_rel=frozenset({"nofollow"}),
        ),
    )

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `html-sanitizer <https://github.com/matthiask/html-sanitizer>`__
      - turbohtml
    - - ``Sanitizer(settings).sanitize(text)``
      - :func:`turbohtml.clean.sanitize` with a :class:`~turbohtml.clean.Policy`
    - - ``settings["tags"]``
      - ``Policy.tags``
    - - ``settings["attributes"]``
      - ``Policy.attributes``
    - - ``add_nofollow``
      - ``Policy.add_link_rel`` (``{"nofollow"}``)
    - - ``sanitize_href`` schemes
      - ``Policy.url_schemes`` (+ ``Policy.allow_relative_urls``)

.. testcode::

    from turbohtml.clean import sanitize, Policy

    print(
        sanitize(
            '<p>Hi <a href="http://x">l</a></p>',
            Policy(
                tags=frozenset({"p", "a"}),
                attributes={"a": frozenset({"href"})},
                add_link_rel=frozenset({"nofollow"}),
            ),
        )
    )

.. testoutput::

    <p>Hi <a href="http://x" rel="nofollow">l</a></p>

**********************
 Gotchas and pitfalls
**********************

- Disallowed-tag default differs. html-sanitizer unwraps a tag outside the allowlist and keeps its children; turbohtml
  escapes it to visible text. For parity, set ``on_disallowed_tag=OnDisallowed.STRIP`` on the policy.
- No whitespace or tag-merging normalization. html-sanitizer collapses whitespace, merges adjacent identical tags, and
  drops empty ones; turbohtml preserves the tree it parsed. Add a post-sanitize walk if you rely on that cleanup.
- The ``element_preprocessors``/``element_postprocessors`` hooks have no direct port. ``attribute_filter`` handles
  value-level rewriting; structural changes need a walk over the returned tree.
- turbohtml's safety baseline is fixed. It removes ``<script>``, ``on*`` handlers, and ``javascript:`` URLs even when a
  policy would admit them, so a policy that names ``script`` in ``tags`` still cannot keep it.
