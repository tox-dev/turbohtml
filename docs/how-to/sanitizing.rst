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
