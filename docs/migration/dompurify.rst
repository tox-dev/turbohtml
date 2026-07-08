################
 From DOMPurify
################

.. package-meta:: npm dompurify cure53/DOMPurify

`DOMPurify <https://github.com/cure53/DOMPurify>`_ is the reference client-side HTML sanitizer: it parses untrusted
markup, walks it against a hardened allowlist, and hands back a string safe to assign to ``innerHTML``. It runs in a
browser or, server-side, on ``jsdom`` through ``isomorphic-dompurify``. Several of its options harden against attacks
the allowlist alone does not stop, and others tune what it keeps: ``SAFE_FOR_TEMPLATES`` neutralizes template-engine
expressions left in the output, ``SANITIZE_NAMED_PROPS`` defuses DOM clobbering by namespacing ``id`` and ``name``
values, ``CUSTOM_ELEMENT_HANDLING`` keeps an app's own custom elements by predicate, and ``USE_PROFILES`` selects which
of HTML, SVG, and MathML to admit.

turbohtml's :mod:`turbohtml.clean` sanitizer covers the same ground behind a frozen, thread-safe
:class:`~turbohtml.clean.Policy`, without a JavaScript runtime. ``Policy.strip_template_markers`` is the port of
``SAFE_FOR_TEMPLATES``, ``Policy.isolate_named_props`` of ``SANITIZE_NAMED_PROPS``, ``custom_element_check`` and
``custom_attribute_check`` of ``CUSTOM_ELEMENT_HANDLING``, and ``allow_html``/``allow_svg``/``allow_mathml`` of
``USE_PROFILES``. Every one runs inside the single C sanitize walk, so a Python service sanitizes in-process instead of
shelling out to Node.

************************
 turbohtml vs DOMPurify
************************

.. list-table::
    :header-rows: 1
    :widths: 24 38 38

    - - Dimension
      - turbohtml
      - DOMPurify
    - - Runtime
      - Python, filtering in a C extension
      - JavaScript, filtering over a browser DOM or ``jsdom``
    - - Configuration
      - One frozen ``Policy``, reusable across threads
      - A config object per ``sanitize`` call
    - - Template safety
      - ``Policy.strip_template_markers``
      - ``SAFE_FOR_TEMPLATES``
    - - Clobbering defense
      - ``Policy.isolate_named_props`` (static ``user-content-`` prefix)
      - ``SANITIZE_NAMED_PROPS`` (static prefix) or ``SANITIZE_DOM`` (live-DOM probe)
    - - Custom elements
      - ``Policy.custom_element_check`` / ``custom_attribute_check`` predicates
      - ``CUSTOM_ELEMENT_HANDLING.tagNameCheck`` / ``attributeNameCheck``
    - - Content profiles
      - ``Policy.allow_html`` / ``allow_svg`` / ``allow_mathml``
      - ``USE_PROFILES: {html, svg, mathMl}``
    - - XML/XHTML output
      - ``Policy.xml`` (well-formed XML string)
      - ``PARSER_MEDIA_TYPE: 'application/xhtml+xml'``, or ``RETURN_DOM`` for a DOM node to reserialize
    - - Typing
      - Fully annotated, ``py.typed``
      - TypeScript definitions
    - - Dependencies
      - None (self-contained C extension)
      - A DOM: a browser, or ``jsdom`` server-side

DOM clobbering
==============

An attacker-controlled ``id`` or ``name`` whose value matches a built-in ``document`` or form property shadows that
property through *named access*. ``<input name="attributes">`` inside a form makes ``form.attributes`` resolve to the
input rather than the real attribute map; ``<img name="body">`` hides ``document.body``; ``<a id="location">`` can stand
in for ``document.location`` in code that reads it by name. Sanitizing against an allowlist keeps the element -- ``id``
and ``name`` are ordinary attributes -- so the collision survives the allowlist and only a dedicated defense removes it.

DOMPurify's ``SANITIZE_NAMED_PROPS`` moves the value out of the property namespace by prefixing every kept ``id`` and
``name`` with the constant string ``user-content-``; a value already carrying the prefix is left alone, so re-running
the sanitizer is a fixpoint. ``Policy.isolate_named_props`` is the same transform, applied in the same C walk that
enforces the allowlist:

.. code-block:: javascript

    DOMPurify.sanitize(dirty, { SANITIZE_NAMED_PROPS: true });

ports to:

.. testcode::

    from turbohtml.clean import sanitize, Policy

    policy = Policy(
        tags=frozenset({"form", "input"}),
        attributes={"form": frozenset({"id"}), "input": frozenset({"name"})},
        isolate_named_props=True,
    )
    print(sanitize('<form id="settings"><input name="attributes"></form>', policy))

.. testoutput::

    <form id="user-content-settings"><input name="user-content-attributes"></form>

The prefix is unconditional on ``id`` and ``name`` -- turbohtml namespaces every kept value rather than probing a live
DOM for a real collision, so it needs no browser and cannot miss a property the running engine happens to expose.
DOMPurify's other mode, ``SANITIZE_DOM``, instead *drops* a colliding attribute after testing ``value in document`` at
sanitize time; that check needs the DOM turbohtml does without, so ``isolate_named_props`` follows the static
``SANITIZE_NAMED_PROPS`` design. The safety baseline is unaffected either way: ``on*`` handlers, scripting elements, and
``javascript:`` URLs are removed regardless of the policy, so isolation is one more layer, never the only one.

Template safety
===============

``SAFE_FOR_TEMPLATES`` collapses ``{{ }}``, ``${ }``, and ``<% %>`` runs so a sanitized value cannot re-inject once a
template engine renders it. ``Policy.strip_template_markers`` is the direct port; see :doc:`the how-to
</how-to/sanitizing>` for a worked example. Both options can be on at once, and both run in the same walk.

Custom elements
===============

DOMPurify's ``CUSTOM_ELEMENT_HANDLING`` keeps an app's own custom elements without adding each to ``ADD_TAGS``: a
``tagNameCheck`` regex or predicate decides whether an unlisted hyphenated element survives, and ``attributeNameCheck``
which of its attributes do. turbohtml takes predicates directly -- ``Policy.custom_element_check`` and
``custom_attribute_check`` -- so a regex is just ``re.compile(...).search``:

.. code-block:: javascript

    DOMPurify.sanitize(dirty, {
      CUSTOM_ELEMENT_HANDLING: {
        tagNameCheck: /^x-/,
        attributeNameCheck: /^data-/,
      },
    });

ports to:

.. testcode::

    import re
    from turbohtml.clean import sanitize, Policy

    policy = Policy(
        tags=frozenset({"p"}),
        custom_element_check=re.compile(r"^x-").search,
        custom_attribute_check=lambda _tag, name: name.startswith("data-"),
    )
    print(sanitize('<p><x-card data-id="7" onclick="steal()">c</x-card></p>', policy))

.. testoutput::

    <p><x-card data-id="7">c</x-card></p>

The one deliberate difference is safety, not shape. DOMPurify's ``attributeNameCheck`` can readmit an ``on*`` handler
when the caller's pattern happens to match it; turbohtml keeps the event-handler, URL-scheme, and ``style`` baseline
unconditional, so the ``onclick`` above is dropped even though ``attributeNameCheck`` is wide open. Set
``allow_customized_builtins`` for DOMPurify's ``allowCustomizedBuiltInElements``, which keeps an ``is`` attribute whose
value names a custom element.

Content profiles
================

``USE_PROFILES`` swaps DOMPurify's default allowlist for whole tag sets per content language. turbohtml keeps the
allowlist as the source of truth and adds three orthogonal namespace gates -- ``allow_html``, ``allow_svg``,
``allow_mathml`` -- so ``{ USE_PROFILES: { svg: true } }`` becomes an allowlist of the SVG tags you want plus
``allow_mathml=False`` to keep the MathML namespace out:

.. testcode::

    policy = Policy(tags=frozenset({"svg", "circle", "math", "mi"}), allow_mathml=False)
    print(sanitize("<svg><circle></circle></svg><math><mi>x</mi></math>", policy))

.. testoutput::

    <svg><circle></circle></svg>&lt;math&gt;&lt;mi&gt;x&lt;/mi&gt;&lt;/math&gt;

XML/XHTML output
================

DOMPurify hands back an HTML string by default; to clean an XHTML dialect you switch it to ``PARSER_MEDIA_TYPE:
'application/xhtml+xml'`` (or take ``RETURN_DOM`` and reserialize the node yourself). turbohtml's port is
``Policy.xml``: the same allowlist walk runs, but the cleared tree serializes as well-formed XML instead of HTML. Every
empty element self-closes, text and attribute values follow the XML escaping rules, foreign SVG and MathML subtrees
declare their namespace, and a kept comment or a stray control character is neutralized, so the output always reparses
through :func:`turbohtml.parse_xml`. It is the fix for the bare ``<br>`` that a bleach-based cleaner has to patch with a
brittle ``.replace("<br>", "<br/>")`` when its consumer -- Reportlab's RML, an ePub content document -- is strict XML:

.. testcode::

    from turbohtml.clean import sanitize, Policy

    policy = Policy(tags=frozenset({"p", "br", "b"}), xml=True)
    print(sanitize("<p>line one<br>line two <b>bold</b></p>", policy))

.. testoutput::

    <p>line one<br/>line two <b>bold</b></p>

Performance
===========

turbohtml isolates and collapses markers in the same C walk that enforces the allowlist, so neither option adds a pass.
The table times both libraries end-to-end; the DOMPurify figure is its Node runner over stdin on
``isomorphic-dompurify``, the cost a Python service pays to reach it as a subprocess, DOM startup included.

.. bench-table::
    :file: bench/dompurify.json
