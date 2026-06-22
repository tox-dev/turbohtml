#####################
 From html-sanitizer
#####################

`html-sanitizer <https://github.com/matthiask/html-sanitizer>`_ already shares turbohtml's allowlist stance, so the move
is a settings-to-:class:`~turbohtml.sanitizer.Policy` translation rather than a rethink. Its ``settings`` dict carries
``tags`` (a set), ``attributes`` (a per-tag dict), ``add_nofollow``, and a ``sanitize_href`` scheme check:

.. code-block:: python

    # html-sanitizer
    from html_sanitizer import Sanitizer

    Sanitizer(
        {"tags": {"a", "p"}, "attributes": {"a": {"href"}}, "add_nofollow": True}
    ).sanitize(text)

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

``tags`` maps to ``Policy.tags``, ``attributes`` to ``Policy.attributes``, ``add_nofollow`` to
``add_link_rel={"nofollow"}``, and ``sanitize_href``'s allowed schemes to ``url_schemes``. Two html-sanitizer features
have no direct port: the whitespace normalization and tag-merging it performs (``empty``, ``separate``, ``whitespace``)
and its ``element_preprocessors``/``element_postprocessors`` hooks. turbohtml's ``attribute_filter`` covers value-level
rewriting, but structural post-processing is left to a walk over the returned tree. html-sanitizer parses through lxml;
turbohtml runs the WHATWG tree builder in C.
