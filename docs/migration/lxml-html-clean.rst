######################
 From lxml-html-clean
######################

`lxml-html-clean <https://github.com/fedora-python/lxml_html_clean>`_ (the ``Cleaner`` split out of ``lxml.html.clean``)
takes the opposite stance to ``turbohtml.sanitizer``: it is a **blocklist**. You toggle off categories of dangerous
content (``scripts``, ``javascript``, ``style``, ``comments``, ``embedded``, ``frames``, ``forms``, ``meta``, ...) and
everything else survives, so a tag the library has not heard of passes through. turbohtml is an **allowlist**: nothing
survives unless a :class:`~turbohtml.sanitizer.Policy` names it, which is why the safety baseline holds against markup
the author never anticipated.

Porting inverts the model. Instead of switching dangerous things off, declare the small set you keep:

.. code-block:: python

    # lxml-html-clean: enumerate what to strip, keep the rest
    from lxml_html_clean import Cleaner

    Cleaner(
        scripts=True, javascript=True, comments=True, style=True, forms=True
    ).clean_html(text)

.. testcode::

    from turbohtml.sanitizer import sanitize, Policy

    print(sanitize(
        "<p>Hi<script>x()</script> <a href='javascript:1'>l</a></p>",
        Policy(tags=frozenset({"p", "a"}), attributes={"a": frozenset({"href"})}),
    ))

.. testoutput::

    <p>Hi&lt;script&gt;x()&lt;/script&gt; <a>l</a></p>

The ``javascript:`` URL is gone because ``http``/``https``/``mailto`` are the only schemes the policy admits, and the
``<script>`` is escaped rather than executed. ``Cleaner``'s ``host_whitelist`` and ``allow_tags`` lists fold into
``Policy.tags`` and ``attribute_filter``, its ``kill_tags`` (drop the element together with its content) maps to
``Policy.remove_with_content``, and its ``add_nofollow`` maps to ``Policy.add_link_rel``. turbohtml scrubs a kept
``style`` attribute against ``Policy.css_properties``, though it drops ``<style>`` elements where ``Cleaner`` scrubs
their text too, and ``Cleaner`` rewrites a disallowed scheme to an empty ``href`` where turbohtml drops the attribute
outright.
