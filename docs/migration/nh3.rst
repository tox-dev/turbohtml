##########
 From nh3
##########

.. package-meta:: nh3 messense/nh3

`nh3 <https://nh3.readthedocs.io>`_ is the Python binding (via PyO3/maturin) for the Rust `ammonia
<https://github.com/rust-ammonia/ammonia>`_ HTML sanitizer, and a common landing spot for projects leaving the
unmaintained bleach. It is an allowlist sanitizer: you declare the tags, attributes, URL schemes, and CSS properties you
trust, and everything else is stripped or dropped. It ships as a single self-contained wheel with no Python runtime
dependencies, plus two helpers -- ``nh3.clean_text`` to escape plain text and ``nh3.is_html`` for a quick content
heuristic.

nh3 stays narrowly a sanitizer. It has no linkifier, and, unlike bleach, no escape-instead-of-strip mode: a disallowed
tag is removed rather than rendered as visible text. ``turbohtml.clean`` covers the same allowlist surface behind a
frozen :class:`~turbohtml.clean.Policy`, adds that escape mode and a companion linkifier, and lives inside a full
WHATWG-conformant parser, so the same tree can be parsed, queried, sanitized, and minified without leaving the library.

******************
 turbohtml vs nh3
******************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - nh3
    - - Scope
      - Full WHATWG parser with a sanitize/linkify/minify/CSS-minify suite
      - HTML sanitization only, plus ``clean_text``/``is_html`` helpers
    - - Feature breadth
      - Escape/strip/remove modes, ``Policy`` presets, linkifier, attribute-prefix/value allowlists, embedded-media host
        allowlist, HTML and CSS minifiers
      - Allowlist sanitize with ``attribute_filter``, style-property filter, attribute-prefix and value allowlists
    - - Performance
      - Native C, leads on the corpus below
      - Native Rust (ammonia core), same tier
    - - Typing
      - Fully annotated Python API with ``.pyi`` stubs
      - Typed via bundled stubs
    - - Dependencies
      - Single C extension, no Python runtime deps
      - Single Rust extension, no Python runtime deps
    - - Maintenance
      - Active, part of a broader HTML toolkit
      - Active binding over the mature ammonia crate

Feature overlap
===============

The allowlist surface ports one-to-one; only the call shape changes.

- Tag allowlist: ``tags=`` -> :class:`Policy.tags <turbohtml.clean.Policy>`.
- Per-tag attributes (with ``"*"`` wildcards): ``attributes=`` -> :class:`Policy.attributes <turbohtml.clean.Policy>`.
- Drop a tag and its whole subtree: ``clean_content_tags=`` -> :class:`Policy.remove_with_content
  <turbohtml.clean.Policy>`.
- Forced ``rel`` tokens on kept links: ``link_rel=`` -> :class:`Policy.add_link_rel <turbohtml.clean.Policy>`.
- URL scheme allowlist: ``url_schemes=`` -> :class:`Policy.url_schemes <turbohtml.clean.Policy>`.
- Comment stripping: ``strip_comments=`` -> :class:`Policy.strip_comments <turbohtml.clean.Policy>`.
- Per-attribute callable ``(tag, attr, value) -> str | None`` that drops or rewrites a value: ``attribute_filter=`` ->
  :class:`Policy.attribute_filter <turbohtml.clean.Policy>`.
- Values forced onto every kept instance of a tag: ``set_tag_attribute_values=`` -> :class:`Policy.set_attributes
  <turbohtml.clean.Policy>`.
- Inline-style property allowlist: ``filter_style_properties=`` -> :class:`Policy.css_properties
  <turbohtml.clean.Policy>`.
- Attribute-name prefix allowlist (the ``data-*`` family): ``generic_attribute_prefixes=`` ->
  :class:`Policy.attribute_prefixes <turbohtml.clean.Policy>`.
- Restrict an attribute to literal values: ``tag_attribute_values=`` -> :class:`Policy.attribute_values
  <turbohtml.clean.Policy>` (narrows an attribute already admitted by ``attributes``; it cannot admit a new one).
- Escape plain text for insertion: ``nh3.clean_text(text)`` -> :func:`turbohtml.escape`, the direct HTML-escaper
  equivalent.

What turbohtml adds
===================

- An escape mode. :class:`~turbohtml.clean.OnDisallowed` is ``ESCAPE`` by default, so a disallowed tag becomes visible
  text (``<x>`` renders as ``&lt;x&gt;``) instead of vanishing; nh3 only strips. ``STRIP`` (drop tag, keep children) and
  ``REMOVE`` (drop the subtree) select nh3-style behavior from one enum.
- Ready-made presets: :meth:`Policy.strict() <turbohtml.clean.Policy.strict>`, :meth:`Policy.basic()
  <turbohtml.clean.Policy.basic>`, and :meth:`Policy.relaxed() <turbohtml.clean.Policy.relaxed>`.
- A ``allow_relative_urls`` toggle to accept or reject scheme-less URLs independently of the scheme allowlist.
- A reusable compiled :class:`~turbohtml.clean.Sanitizer`: build it once from a frozen :class:`~turbohtml.clean.Policy`
  and call it from any thread.
- A companion linkifier, :func:`turbohtml.clean.linkify`, for auto-linking plain URLs; nh3 has none.
- HTML and CSS minifiers (:func:`~turbohtml.clean.minify`, :func:`~turbohtml.clean.minify_css`) in the same module, over
  the same parse tree.

What nh3 has that turbohtml does not
====================================

- ``nh3.is_html(text)`` -- a heuristic bool for whether a string contains HTML. No turbohtml equivalent.

Performance
===========

.. bench-table::
    :file: bench/nh3.json

The corpus benches nh3's allowlist sanitizer against :func:`turbohtml.clean.sanitize` and nh3's ``clean_text`` HTML
escaper against :func:`turbohtml.escape`. turbohtml stays in the same native tier as the Rust binding and leads it on
both: sanitizing runs nearly 4x faster, and escaping runs 2.4x to 65x faster depending on how much of the input needs
rewriting.

****************
 How to migrate
****************

Swap the import and wrap the keyword arguments in a :class:`~turbohtml.clean.Policy`. nh3's flat keyword call becomes a
frozen config object plus :func:`~turbohtml.clean.sanitize`.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `nh3 <https://nh3.readthedocs.io/>`__
      - turbohtml
    - - ``nh3.clean(text, ...)``
      - :func:`turbohtml.clean.sanitize` with a :class:`~turbohtml.clean.Policy`
    - - ``tags=``, ``attributes=``
      - ``Policy.tags``, ``Policy.attributes``
    - - ``clean_content_tags=``
      - ``Policy.remove_with_content``
    - - ``link_rel=``
      - ``Policy.add_link_rel``
    - - ``url_schemes=``
      - ``Policy.url_schemes``
    - - ``strip_comments=``
      - ``Policy.strip_comments``
    - - ``attribute_filter=``
      - ``Policy.attribute_filter``
    - - ``set_tag_attribute_values=``
      - ``Policy.set_attributes``
    - - ``filter_style_properties=``
      - ``Policy.css_properties``
    - - ``generic_attribute_prefixes=``
      - ``Policy.attribute_prefixes``
    - - ``tag_attribute_values=``
      - ``Policy.attribute_values``
    - - (drops disallowed tags)
      - :class:`~turbohtml.clean.OnDisallowed` (``ESCAPE`` by default; ``STRIP`` / ``REMOVE`` for nh3-style dropping)

.. code-block:: python

    # nh3
    import nh3

    nh3.clean(text, tags={"a"}, attributes={"a": {"href"}})

    # turbohtml
    from turbohtml.clean import sanitize, Policy, OnDisallowed

    sanitize(text, Policy(tags=frozenset({"a"}), attributes={"a": frozenset({"href"})}))

    # match nh3's default of dropping (not escaping) disallowed tags
    sanitize(
        text,
        Policy(
            tags=frozenset({"a"}),
            attributes={"a": frozenset({"href"})},
            on_disallowed_tag=OnDisallowed.STRIP,
        ),
    )

**********************
 Gotchas and pitfalls
**********************

- Disallowed-tag default differs. nh3 strips a disallowed tag and keeps its children; turbohtml escapes it to visible
  text by default. Set ``on_disallowed_tag=OnDisallowed.STRIP`` to reproduce nh3's behavior exactly.
- Set-typed fields must be sets. ``tags``, ``url_schemes``, ``remove_with_content``, and ``css_properties`` expect a
  ``set``/``frozenset``; passing another type raises :class:`TypeError`. nh3 accepts any iterable for the equivalent
  arguments.
- Attribute allowlisting is by exact name, a per-tag ``"*"`` wildcard, or a name prefix in ``attribute_prefixes`` (nh3's
  ``generic_attribute_prefixes``, e.g. ``"data-"`` for every ``data-*``). ``attribute_values`` restricts a kept
  attribute to literal values; it narrows an attribute ``attributes`` already admits and cannot admit a new one.
- ``sanitize`` takes an HTML fragment and returns a fragment; it does not add ``<html>``/``<body>`` scaffolding,
  matching nh3's fragment-in, fragment-out contract.
- Reuse the compiled :class:`~turbohtml.clean.Sanitizer` when sanitizing many inputs with one policy; each bare
  :func:`~turbohtml.clean.sanitize` call recompiles the policy.
