#####################
 From bleach (clean)
#####################

bleach is end of life and has no maintained successor for its sanitizer, so ``turbohtml.sanitizer`` takes its place. The
bleach-compatible shim keeps ``clean``'s signature so the import is the only change:

.. code-block:: python

    # bleach
    from bleach import clean

    # turbohtml
    from turbohtml.migration.bleach import clean

``clean(text, tags=..., attributes=..., protocols=..., strip=..., strip_comments=...)`` maps onto a
:class:`~turbohtml.sanitizer.Policy`. ``attributes`` accepts bleach's list, per-tag dict, or callable forms; ``strip``
chooses between dropping a disallowed tag and keeping its children (``True``) and escaping it (``False``, the default):

.. testcode::

    from turbohtml.migration.bleach import clean

    print(clean("<p>Hi <a href='http://x'>link</a></p><script>evil()</script>"))

.. testoutput::

    &lt;p&gt;Hi <a href="http://x">link</a>&lt;/p&gt;&lt;script&gt;evil()&lt;/script&gt;

For new code prefer the native :class:`~turbohtml.sanitizer.Policy`/:class:`~turbohtml.sanitizer.Sanitizer` API: a
frozen, thread-safe policy (bleach's ``clean`` had a documented thread-safety footgun), an
:class:`~turbohtml.sanitizer.OnDisallowed` enum that names escape/strip/remove where bleach overloaded two booleans, and
an ``attribute_filter`` that rewrites or drops a value where bleach's callable only returned a bool. One difference is
deliberate and load-bearing: turbohtml's safety baseline (``<script>``, ``on*`` handlers, ``javascript:`` URLs) is not
configurable, so even a permissive ``attributes`` callable cannot re-admit them, where bleach faithfully kept whatever
you allowed. The native sanitizer scrubs the ``style`` attribute against ``Policy.css_properties`` (the safe set
bleach's ``css_sanitizer`` defaults to), so an allowed ``style`` keeps only allowlisted declarations; the
bleach-compatible ``clean`` shim does not yet take a ``css_sanitizer`` argument (it raises), and ``<style>`` element
contents are dropped rather than scrubbed.
