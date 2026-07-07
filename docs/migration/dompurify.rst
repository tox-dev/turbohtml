################
 From DOMPurify
################

`DOMPurify <https://github.com/cure53/DOMPurify>`_ is the reference client-side HTML sanitizer: it parses untrusted
markup, walks it against a hardened allowlist, and hands back a string safe to assign to ``innerHTML``. It runs in a
browser or, server-side, on ``jsdom`` through ``isomorphic-dompurify``. Two of its options harden against attacks the
allowlist alone does not stop: ``SAFE_FOR_TEMPLATES`` neutralizes template-engine expressions left in the output, and
``SANITIZE_NAMED_PROPS`` defuses DOM clobbering by namespacing ``id`` and ``name`` values.

turbohtml's :mod:`turbohtml.clean` sanitizer covers the same ground behind a frozen, thread-safe
:class:`~turbohtml.clean.Policy`, without a JavaScript runtime. ``Policy.strip_template_markers`` is the port of
``SAFE_FOR_TEMPLATES``, and ``Policy.isolate_named_props`` the port of ``SANITIZE_NAMED_PROPS``. Both run inside the
single C sanitize walk, so a Python service sanitizes in-process instead of shelling out to Node.

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

Performance
===========

turbohtml isolates and collapses markers in the same C walk that enforces the allowlist, so neither option adds a pass.
The table times both libraries end-to-end; the DOMPurify figure is its Node runner over stdin on
``isomorphic-dompurify``, the cost a Python service pays to reach it as a subprocess, DOM startup included.

.. bench-table::
    :file: bench/dompurify.json
