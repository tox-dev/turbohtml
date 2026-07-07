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
