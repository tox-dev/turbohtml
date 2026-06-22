#####################
 From html-sanitizer
#####################

.. image:: https://static.pepy.tech/badge/html-sanitizer
    :alt: html-sanitizer downloads
    :target: https://pepy.tech/project/html-sanitizer

`html-sanitizer <https://github.com/matthiask/html-sanitizer>`_ is an allowlist sanitizer over lxml, configured with a
``settings`` dict carrying ``tags`` (a set), ``attributes`` (a per-tag dict), ``add_nofollow``, a ``sanitize_href``
scheme check, and whitespace/tag-merging normalization.

***************
 Why turbohtml
***************

``turbohtml.sanitizer`` shares the allowlist stance, so the move is a settings-to-:class:`~turbohtml.sanitizer.Policy`
translation rather than a rethink, but it adds full static typing, a frozen thread-safe policy, and the WHATWG tree
builder in C instead of an lxml parse, leading html-sanitizer by one to two orders of magnitude:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - sanitize
      - turbohtml
      - html-sanitizer
      - speed-up
    - - comment (1 link, 1 script)
      - 1.5 µs
      - 45.3 µs
      - 30.5x
    - - post (4 KiB)
      - 42.1 µs
      - 1504 µs
      - 35.8x

*************
 The renames
*************

.. code-block:: python

    # html-sanitizer
    from html_sanitizer import Sanitizer

    Sanitizer(
        {"tags": {"a", "p"}, "attributes": {"a": {"href"}}, "add_nofollow": True}
    ).sanitize(text)

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - html-sanitizer
      - turbohtml
    - - ``Sanitizer(settings).sanitize(text)``
      - :func:`turbohtml.sanitizer.sanitize` with a :class:`~turbohtml.sanitizer.Policy`
    - - ``tags``
      - ``Policy.tags``
    - - ``attributes``
      - ``Policy.attributes``
    - - ``add_nofollow``
      - ``Policy.add_link_rel`` (``{"nofollow"}``)
    - - ``sanitize_href`` schemes
      - ``Policy.url_schemes``

.. testcode::

    from turbohtml.sanitizer import sanitize, Policy

    print(sanitize(
        '<p>Hi <a href="http://x">l</a></p>',
        Policy(
            tags=frozenset({"p", "a"}),
            attributes={"a": frozenset({"href"})},
            add_link_rel=frozenset({"nofollow"}),
        ),
    ))

.. testoutput::

    <p>Hi <a href="http://x" rel="nofollow">l</a></p>

**********
 Pitfalls
**********

- The whitespace normalization and tag-merging html-sanitizer performs (``empty``, ``separate``, ``whitespace``) has no
  direct port; do it with a walk over the returned tree.
- Its ``element_preprocessors``/``element_postprocessors`` hooks have no direct port either. turbohtml's
  ``attribute_filter`` covers value-level rewriting, but structural post-processing is left to a walk over the tree.
