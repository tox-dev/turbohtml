####################
 From sanitize-html
####################

.. package-meta:: npm sanitize-html apostrophecms/sanitize-html

`sanitize-html <https://github.com/apostrophecms/sanitize-html>`_ is the standard Node HTML sanitizer: you declare the
tags, attributes, URL schemes, and CSS properties you trust, and everything else is stripped. Its distinguishing feature
is ``transformTags`` -- a map that renames an element (and optionally rewrites its attributes) during the sanitize pass,
with a ``simpleTransform(tagName, attribs, merge)`` helper for the common rename-and-add case. Projects that ran
sanitization in a Node service, or in a Python service shelling out to Node, reach for it because bleach and its
successors historically had no equivalent rename step.

turbohtml's :mod:`turbohtml.clean` sanitizer covers the same allowlist surface behind a frozen, thread-safe
:class:`~turbohtml.clean.Policy`, and ``Policy.transform_tags`` is the direct port of ``transformTags``: it renames HTML
elements during the same single C walk, with :class:`~turbohtml.clean.Transform` playing the role of
``simpleTransform``. Moving the sanitize step into Python drops the Node subprocess, and the second language with it.

****************************
 turbohtml vs sanitize-html
****************************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - sanitize-html
    - - Runtime
      - Python, filtering in a C extension
      - Node.js, filtering in JavaScript over htmlparser2
    - - Rename step
      - ``Policy.transform_tags`` map, ``Transform`` for rename-and-add
      - ``transformTags`` map, ``simpleTransform`` for rename-and-add
    - - Rename safety
      - Renamed element re-checked against the allowlist and the unconditional baseline
      - Renamed element re-checked against ``allowedTags``/``allowedAttributes``
    - - Configuration
      - One frozen ``Policy``, reusable across threads
      - A plain options object per call
    - - Typing
      - Fully annotated, ``py.typed``
      - TypeScript definitions
    - - Dependencies
      - None (self-contained C extension)
      - htmlparser2, and its transitive tree

Feature overlap
===============

The transform surface ports one-to-one:

- ``transformTags: { b: 'strong' }`` (rename) -> ``transform_tags={"b": "strong"}``.
- ``transformTags: { center: sanitizeHtml.simpleTransform('div', { class: 'x' }) }`` (rename and add attributes) ->
  ``transform_tags={"center": Transform("div", {"class": "x"})}``.
- ``allowedTags`` / ``allowedAttributes`` -> ``Policy.tags`` / ``Policy.attributes``.
- ``allowedSchemes`` -> ``Policy.url_schemes``; ``allowedStyles`` -> ``Policy.allowed_styles``.

Both apply the rename before the allowlist, so a transform decides an element's name but never its safety: mapping a tag
to ``script`` still drops it, and an attribute added by a transform is scrubbed like the element's own. turbohtml keeps
that guarantee under an *unconditional* baseline -- ``on*`` handlers, scripting elements, and ``javascript:`` URLs are
removed regardless of the policy -- so a transform can smuggle neither a disallowed tag nor an unscrubbed attribute.

The one shape difference: ``transformTags`` also accepts an arbitrary callback returning ``{ tagName, attribs, text }``,
and a ``'*'`` entry that runs on every tag. ``transform_tags`` is a declarative per-tag map (a string or a
:class:`~turbohtml.clean.Transform`); it renames and adds attributes without running caller code mid-walk, and per-tag
rules cover the presentational-tag modernization ``simpleTransform`` was built for. Only HTML elements are transformed.

Porting a transform
===================

A sanitize-html config that modernizes legacy presentational tags:

.. code-block:: javascript

    const clean = sanitizeHtml(dirty, {
      allowedTags: ["strong", "em", "div"],
      allowedAttributes: { div: ["class"] },
      transformTags: {
        b: "strong",
        i: "em",
        center: sanitizeHtml.simpleTransform("div", { class: "legacy" }),
      },
    });

ports to:

.. testcode::

    from turbohtml.clean import sanitize, Policy, Transform

    policy = Policy(
        tags=frozenset({"strong", "em", "div"}),
        attributes={"div": frozenset({"class"})},
        transform_tags={
            "b": "strong",
            "i": "em",
            "center": Transform("div", {"class": "legacy"}),
        },
    )
    print(sanitize("<center><b>bold</b> and <i>italic</i></center>", policy))

.. testoutput::

    <div class="legacy"><strong>bold</strong> and <em>italic</em></div>

Performance
===========

turbohtml renames in the same C walk that sanitizes, so a transform adds no extra pass. The table times both libraries
end-to-end on a document dense in the presentational tags a transform rewrites; the sanitize-html figure is its Node
runner over stdin, the cost a Python service pays to reach it as a subprocess.

.. bench-table::
    :file: bench/sanitize-html.json
