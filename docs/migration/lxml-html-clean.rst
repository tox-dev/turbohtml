######################
 From lxml-html-clean
######################

.. package-meta:: lxml_html_clean fedora-python/lxml_html_clean

`lxml-html-clean <https://github.com/fedora-python/lxml_html_clean>`_ is the ``Cleaner`` that used to live in
``lxml.html.clean``, split into its own package when lxml dropped the module. It cleans HTML by **blocklist**: you
toggle off categories of dangerous content (``scripts``, ``javascript``, ``style``, ``comments``, ``embedded``,
``frames``, ``forms``, ``meta``, ``annoying_tags``, ...) and everything the toggles do not name survives. A tag the
library has never heard of passes through untouched. ``Cleaner`` runs in Python over an lxml element tree parsed by
libxml2, so it inherits lxml's runtime dependency and its non-WHATWG HTML parser. It is the standard choice for projects
already built on lxml that need to scrub user HTML in place.

``turbohtml.clean`` covers the same ground from the other direction: :func:`~turbohtml.clean.sanitize` filters against
an **allowlist** driven by a :class:`~turbohtml.clean.Policy`, parses with the WHATWG algorithm browsers use, and runs
the filtering walk in C.

******************************
 turbohtml vs lxml-html-clean
******************************

.. list-table::
    :header-rows: 1
    :widths: 16 42 42

    - - Dimension
      - turbohtml
      - lxml-html-clean
    - - Scope
      - Allowlist HTML sanitizer, plus linkify and HTML/CSS minify in one module
      - Blocklist ``Cleaner`` extracted from ``lxml.html.clean``; sanitization only
    - - Feature breadth
      - Policy presets (``strict``/``basic``/``relaxed``), per-tag attribute allowlists, attribute-prefix/value
        allowlists, embedded-media ``host_whitelist``, ``style`` scrubbing, link ``rel`` injection, attribute-filter
        hook
      - ~20 category toggles, ``host_whitelist`` for embedded media, ``safe_attrs``, ``add_nofollow``, in-place tree
        mutation
    - - Performance
      - C filtering walk; order of magnitude faster (see below)
      - Python traversal over a libxml2-parsed lxml tree
    - - Typing
      - Fully type annotated
      - No type annotations shipped
    - - Dependencies
      - Self-contained C extension, no runtime dependencies
      - Requires lxml (libxml2 / libxslt)
    - - Maintenance
      - Actively developed
      - Community-maintained successor to the removed ``lxml.html.clean``

Feature overlap
===============

The shared surface ports directly:

- Strip scripting: ``Cleaner(scripts=True, javascript=True)`` becomes the non-overridable safety baseline that removes
  script elements, event-handler attributes, and ``javascript:`` URLs on every policy.
- Restrict the kept tag set: ``allow_tags=`` maps to :class:`~turbohtml.clean.Policy` ``tags``.
- Drop an element with its whole subtree: ``kill_tags=`` maps to ``Policy.remove_with_content``.
- Drop comments: ``comments=True`` maps to ``Policy.strip_comments`` (on by default).
- Constrain attributes: ``safe_attrs_only`` / ``safe_attrs=`` map to the per-tag ``Policy.attributes`` allowlist.
- Force ``rel`` tokens on links: ``add_nofollow=True`` maps to ``Policy.add_link_rel`` (e.g.
  ``frozenset({"nofollow"})``).
- Remove forms, frames, and embedded media: leave those tags out of ``Policy.tags`` instead of toggling ``forms``,
  ``frames``, ``embedded``.
- Restrict embedded media to named hosts: ``host_whitelist=`` maps to :class:`Policy.media_hosts
  <turbohtml.clean.Policy>`, which drops an ``audio``/``video``/``source``/``track`` ``src`` whose URL host is not on
  the allowlist.

What turbohtml adds
===================

- Allowlist model: nothing survives unless the policy names it, so markup the author never anticipated cannot slip
  through a missing toggle.
- WHATWG parsing: the input is parsed by the same algorithm browsers use, so the cleaned tree matches what a browser
  would build, not what libxml2's HTML parser produces.
- ``style`` attribute scrubbing against ``Policy.css_properties``, dropping any declaration whose property is not
  allowed.
- ``Policy.attribute_filter``, a per-attribute callback that gets the last word on every surviving value, and
  ``Policy.add_link_rel`` to inject ``rel`` tokens.
- ``Policy.on_disallowed_tag`` (:class:`~turbohtml.clean.OnDisallowed`) to choose escape, strip, or remove for a tag
  outside the allowlist.
- Companion :func:`~turbohtml.clean.linkify`, :func:`~turbohtml.clean.minify`, and value-safe
  :func:`~turbohtml.clean.minify_css` in the same module.

What lxml-html-clean has that turbohtml does not
================================================

- In-place cleaning of an existing lxml element or subtree: ``Cleaner`` mutates the tree you already hold.
  :func:`~turbohtml.clean.sanitize` takes an HTML string and returns an HTML string, so there is no in-tree equivalent.
- ``annoying_tags``: a named toggle for presentational cruft (``blink``, ``marquee``). turbohtml has no dedicated flag;
  exclude those tags from ``Policy.tags`` (the allowlist already drops them by default).

Performance
===========

``turbohtml.clean`` runs the filtering walk in C rather than over an lxml tree, leading the blocklist cleaner by an
order of magnitude:

.. bench-table::
    :file: bench/lxml-html-clean.json

****************
 How to migrate
****************

Swap the import and invert the model: instead of switching dangerous categories off, declare the small set you keep.

.. code-block:: python

    # lxml-html-clean: enumerate what to strip, keep the rest
    from lxml_html_clean import Cleaner

    Cleaner(scripts=True, javascript=True, comments=True, style=True, forms=True).clean_html(text)

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `lxml-html-clean <https://lxml-html-clean.readthedocs.io/>`__
      - turbohtml
    - - ``Cleaner(...).clean_html(text)``
      - :func:`turbohtml.clean.sanitize` with a :class:`~turbohtml.clean.Policy`
    - - ``allow_tags=``
      - ``Policy.tags``
    - - ``safe_attrs_only=``, ``safe_attrs=``
      - ``Policy.attributes``
    - - ``kill_tags=`` (drop element with content)
      - ``Policy.remove_with_content``
    - - ``comments=True``
      - ``Policy.strip_comments``
    - - ``add_nofollow=True``
      - ``Policy.add_link_rel``
    - - ``host_whitelist=``
      - ``Policy.media_hosts``

.. testcode::

    from turbohtml.clean import sanitize, Policy

    print(
        sanitize(
            "<p>Hi<script>x()</script> <a href='javascript:1'>l</a></p>",
            Policy(tags=frozenset({"p", "a"}), attributes={"a": frozenset({"href"})}),
        )
    )

.. testoutput::

    <p>Hi&lt;script&gt;x()&lt;/script&gt; <a>l</a></p>

The ``javascript:`` URL is gone because ``http``/``https``/``mailto`` are the only schemes the policy admits, and the
``<script>`` is escaped rather than executed.

**********************
 Gotchas and pitfalls
**********************

- ``host_whitelist`` in ``Cleaner`` gates ``iframe``/``embed``; turbohtml escapes those framing elements outright (they
  are never kept), so ``Policy.media_hosts`` instead gates the ``src`` of the media elements turbohtml can keep --
  ``audio``, ``video``, ``source``, ``track`` -- dropping a ``src`` whose host is not on the lowercase allowlist.
- turbohtml scrubs a kept ``style`` attribute against ``Policy.css_properties`` but drops ``<style>`` elements, where
  ``Cleaner`` scrubs their text and keeps the element.
- ``Cleaner`` rewrites a disallowed scheme to an empty ``href``; turbohtml drops the attribute outright.
- A tag outside the allowlist is escaped by default (``OnDisallowed.ESCAPE``), so ``<script>`` shows up as text rather
  than vanishing; pass ``on_disallowed_tag=OnDisallowed.STRIP`` or ``REMOVE`` to match ``Cleaner``'s removal behavior.
- turbohtml strips comments by default (``strip_comments=True``); ``Cleaner`` keeps them unless you set
  ``comments=True``.
- turbohtml parses per WHATWG, so malformed markup yields the tree a browser builds; ``Cleaner`` parses with libxml2 and
  can produce a different structure for the same broken input.
