#########################
 Sanitize untrusted HTML
#########################

*************************
 Sanitize untrusted HTML
*************************

To clean user-submitted HTML the way ``bleach.clean`` did, use :func:`turbohtml.sanitizer.sanitize`. A
:class:`~turbohtml.sanitizer.Policy` says what to keep (here the ``relaxed`` preset for typical user content), and a
non-overridable baseline drops scripting and ``javascript:`` URLs no matter what the policy allows:

.. testcode::

    from turbohtml.sanitizer import sanitize, Policy

    print(sanitize("<p>Hi <a href='javascript:alert(1)'>link</a></p><script>evil()</script>", Policy.relaxed()))

.. testoutput::

    <p>Hi <a>link</a></p>&lt;script&gt;evil()&lt;/script&gt;
