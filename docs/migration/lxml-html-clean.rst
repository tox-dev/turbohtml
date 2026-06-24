######################
 From lxml-html-clean
######################

.. image:: https://static.pepy.tech/badge/lxml_html_clean/month
    :alt: lxml-html-clean monthly downloads
    :target: https://pepy.tech/project/lxml_html_clean

`lxml-html-clean <https://github.com/fedora-python/lxml_html_clean>`_ is the ``Cleaner`` split out of
``lxml.html.clean``. It is a **blocklist**: you toggle off categories of dangerous content (``scripts``, ``javascript``,
``style``, ``comments``, ``embedded``, ``frames``, ``forms``, ``meta``, ...) and everything else survives, so a tag the
library has not heard of passes through.

***************
 Why turbohtml
***************

``turbohtml.sanitizer`` takes the opposite, safer stance: it is an **allowlist**, so nothing survives unless a
:class:`~turbohtml.sanitizer.Policy` names it, which is why the safety baseline holds against markup the author never
anticipated. It is fully type annotated and runs the filtering walk in C rather than over an lxml tree, leading the
blocklist cleaner by an order of magnitude:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - sanitize
      - turbohtml
      - lxml-html-clean
    - - comment (1 link, 1 script)
      - 1.5 µs
      - 19.4 µs (13.0x)
    - - post (4 KiB)
      - 42.1 µs
      - 497 µs (11.8x)

*************
 The renames
*************

Porting inverts the model. Instead of switching dangerous things off, declare the small set you keep:

.. code-block:: python

    # lxml-html-clean: enumerate what to strip, keep the rest
    from lxml_html_clean import Cleaner

    Cleaner(scripts=True, javascript=True, comments=True, style=True, forms=True).clean_html(text)

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - lxml-html-clean
      - turbohtml
    - - ``Cleaner(...).clean_html(text)``
      - :func:`turbohtml.sanitizer.sanitize` with a :class:`~turbohtml.sanitizer.Policy`
    - - ``host_whitelist=``, ``allow_tags=``
      - ``Policy.tags`` and ``Policy.attribute_filter``
    - - ``kill_tags=`` (drop element with content)
      - ``Policy.remove_with_content``
    - - ``add_nofollow=``
      - ``Policy.add_link_rel``

.. testcode::

    from turbohtml.sanitizer import sanitize, Policy

    print(
        sanitize(
            "<p>Hi<script>x()</script> <a href='javascript:1'>l</a></p>",
            Policy(tags=frozenset({"p", "a"}), attributes={"a": frozenset({"href"})}),
        )
    )

.. testoutput::

    <p>Hi&lt;script&gt;x()&lt;/script&gt; <a>l</a></p>

The ``javascript:`` URL is gone because ``http``/``https``/``mailto`` are the only schemes the policy admits, and the
``<script>`` is escaped rather than executed.

**********
 Pitfalls
**********

- turbohtml scrubs a kept ``style`` attribute against ``Policy.css_properties`` but drops ``<style>`` elements, where
  ``Cleaner`` scrubs their text too.
- ``Cleaner`` rewrites a disallowed scheme to an empty ``href`` where turbohtml drops the attribute outright.
