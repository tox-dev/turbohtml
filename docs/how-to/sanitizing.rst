#########################
 Sanitize untrusted HTML
#########################

Clean untrusted HTML against an allowlist with :func:`turbohtml.clean.sanitize`, the ``bleach.clean`` successor, keeping
only a safe subset of tags, attributes, and URL schemes.

*************************
 Sanitize untrusted HTML
*************************

To clean user-submitted HTML the way ``bleach.clean`` did, use :func:`turbohtml.clean.sanitize`. A
:class:`~turbohtml.clean.Policy` says what to keep (here the ``relaxed`` preset for typical user content), and a
non-overridable baseline drops scripting and ``javascript:`` URLs no matter what the policy allows:

.. testcode::

    from turbohtml.clean import sanitize, Policy

    print(sanitize("<p>Hi <a href='javascript:alert(1)'>link</a></p><script>evil()</script>", Policy.relaxed()))

.. testoutput::

    <p>Hi <a>link</a></p>&lt;script&gt;evil()&lt;/script&gt;

*************************************
 Keep the output safe for a template
*************************************

When sanitized HTML is later rendered through a client-side template engine (Angular, Vue, Mustache, EJS, ERB), a
surviving ``{{ ... }}``, ``${ ... }``, or ``<% ... %>`` is a second injection point: the engine evaluates it after the
sanitizer has passed. Set ``strip_template_markers`` to collapse every such run, in kept text and attribute values, to a
single space, matching DOMPurify's ``SAFE_FOR_TEMPLATES``.

.. code-block:: python

    from turbohtml.clean import sanitize, Policy

    policy = Policy.relaxed()
    policy = Policy(tags=policy.tags, attributes=policy.attributes, strip_template_markers=True)
    print(sanitize("<p title='{{x}}'>Hi {{ user.name }}</p>", policy))
    # <p title=" ">Hi  </p>

***************************
 Neutralize DOM clobbering
***************************

When you allow ``id`` or ``name`` on kept elements, an attacker can set a value that collides with a built-in
``document`` or form property and shadow it through named access -- ``<input name="attributes">`` makes
``form.attributes`` resolve to the input, ``<img name="body">`` hides ``document.body``. The allowlist keeps the
attribute because it is otherwise ordinary. Set ``isolate_named_props`` to prefix every kept ``id`` and ``name`` value
with ``user-content-``, moving it out of the property namespace so no value can collide, matching DOMPurify's
``SANITIZE_NAMED_PROPS``. An already-prefixed value is left alone, so re-sanitizing is a fixpoint.

.. testcode::

    from turbohtml.clean import sanitize, Policy

    policy = Policy(
        tags=frozenset({"a", "input"}),
        attributes={"a": frozenset({"id", "href"}), "input": frozenset({"name"})},
        isolate_named_props=True,
    )
    print(sanitize('<a id="location" href="http://x/">x</a><input name="attributes">', policy))

.. testoutput::

    <a id="user-content-location" href="http://x/">x</a><input name="user-content-attributes">

*********************************************
 Restrict inline styles to known-good values
*********************************************

``css_properties`` allowlists property *names*; ``allowed_styles`` narrows further by *value*, the way sanitize-html's
``allowedStyles`` does. Key it ``{tag: {property: [pattern, ...]}}``, with ``"*"`` as a tag matching every element. A
declaration survives only when its property is listed for the element's tag or ``"*"`` and its value matches one of the
patterns (an unanchored :func:`re.search`). This runs on top of ``css_properties`` and the dangerous-value baseline: a
property must still be in ``css_properties``, and ``expression()`` or a ``url()`` with a disallowed scheme is dropped
even if a pattern would admit it.

.. testcode::

    from turbohtml.clean import sanitize, Policy

    policy = Policy(
        tags=frozenset({"p"}),
        attributes={"p": frozenset({"style"})},
        css_properties=frozenset({"color", "text-align"}),
        allowed_styles={"*": {"color": [r"^#[0-9a-f]{3,6}$"], "text-align": [r"^left$|^center$|^right$"]}},
    )
    print(sanitize('<p style="color: #ff0000; text-align: justify; font-size: 40px">Hi</p>', policy))

.. testoutput::

    <p style="color: #ff0000">Hi</p>

******************************
 Rename tags while sanitizing
******************************

To rewrite a tag as it is cleaned -- ``<b>`` to ``<strong>``, or the deprecated ``<center>`` to a ``<div>`` -- set
``transform_tags``, sanitize-html's ``transformTags``. Key it by source tag: a bare string renames, and a
:class:`~turbohtml.clean.Transform` renames and adds attributes. The rename runs before the allowlist, so the renamed
element is re-checked from scratch; a transform sets an element's name, never its safety. Mapping a tag to a disallowed
or unsafe target (``script``) still drops it, and an added attribute is scrubbed with the element's own, so it must be
allowlisted to survive.

.. testcode::

    from turbohtml.clean import sanitize, Policy, Transform

    policy = Policy(
        tags=frozenset({"strong", "em", "div"}),
        attributes={"div": frozenset({"class"})},
        transform_tags={"b": "strong", "i": "em", "center": Transform("div", {"class": "legacy"})},
    )
    print(sanitize("<center><b>bold</b> and <i>italic</i></center>", policy))

.. testoutput::

    <div class="legacy"><strong>bold</strong> and <em>italic</em></div>

************************************
 Allow an app's own custom elements
************************************

To keep your own custom elements (``<my-widget>``, ``<x-card>``) without enumerating every one in ``tags``, give the
policy a matcher, DOMPurify's ``CUSTOM_ELEMENT_HANDLING``. ``custom_element_check`` receives an unlisted element's
lowercased tag name and returns true to keep it; ``custom_attribute_check`` receives ``(tag, attribute_name)`` and
decides which of a kept custom element's attributes survive. Only basic custom-element names (hyphenated, not a reserved
name like ``annotation-xml``) reach the matcher, and the safety baseline still runs on whatever it keeps -- ``on*``
handlers, ``javascript:`` URLs, and dangerous styles are dropped regardless.

.. testcode::

    from turbohtml.clean import sanitize, Policy

    policy = Policy(
        tags=frozenset({"p"}),
        custom_element_check=lambda tag: tag.startswith("x-"),
        custom_attribute_check=lambda _tag, name: name.startswith("data-"),
    )
    print(sanitize('<p><x-rating data-stars="5" onclick="x">stars</x-rating><y-ad>no</y-ad></p>', policy))

.. testoutput::

    <p><x-rating data-stars="5">stars</x-rating>&lt;y-ad&gt;no&lt;/y-ad&gt;</p>

Pass ``re.compile(r"^x-").search`` as the matcher to drive it from a regular expression, and set
``allow_customized_builtins`` to also keep an ``is`` attribute whose value names a custom element (``<button
is="x-fancy">``).

***************************************
 Allow SVG or MathML but not the other
***************************************

``allow_html``, ``allow_svg``, and ``allow_mathml`` gate each content language independently, DOMPurify's
``USE_PROFILES``. They default on, so an allowlist governs each namespace as before; turn one off to drop that whole
namespace even when its tags are in ``tags``. Here SVG is kept and MathML dropped:

.. testcode::

    policy = Policy(tags=frozenset({"svg", "circle", "math", "mi"}), allow_mathml=False)
    print(sanitize("<svg><circle></circle></svg><math><mi>x</mi></math>", policy))

.. testoutput::

    <svg><circle></circle></svg>&lt;math&gt;&lt;mi&gt;x&lt;/mi&gt;&lt;/math&gt;

**************************************
 Trust the first pass, do not reparse
**************************************

Sanitizing is a single parse pass, like DOMPurify. Its output is safe to insert into a DOM as it stands. Do not parse it
again with a different engine or in a different context and then trust that second tree.

HTML parsing is not a fixpoint: serialize a tree and reparse it, and you can get a different tree. In foreign content a
``</p>`` or ``</br>`` end tag builds an HTML element *inside* the SVG/MathML root, while the matching start tag on a
later parse breaks *out* of it; a raw carriage return in text also becomes a newline on reparse. ``sanitize`` resolves
all of that in its one pass, so the string it returns is inert. Reparsing and reserializing that string can yield a
different string that is still inert. The guarantee is inertness, not byte-stable output across a round trip.

*****************************
 See what the policy dropped
*****************************

:func:`turbohtml.clean.sanitize_report` sanitizes and also hands back what it removed, the way DOMPurify populates
``DOMPurify.removed``. Each dropped element or stripped attribute becomes one :class:`~turbohtml.clean.Removed` record,
in the order the walk reached it, so a policy can be tuned against evidence instead of guesswork.

.. testcode::

    from turbohtml.clean import sanitize_report, Policy

    html, removed = sanitize_report('<p onmouseover="x">Hi <b onclick="y">there</b></p>', Policy.relaxed())
    print(removed)

.. testoutput::

    [Removed(tag='p', attribute='onmouseover'), Removed(tag='b', attribute='onclick')]
