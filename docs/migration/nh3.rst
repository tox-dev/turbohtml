##########
 From nh3
##########

.. image:: https://static.pepy.tech/badge/nh3/month
    :alt: nh3 monthly downloads
    :target: https://pepy.tech/project/nh3

`nh3 <https://nh3.readthedocs.io>`_ is the Python binding for the Rust `ammonia
<https://github.com/rust-ammonia/ammonia>`_ HTML sanitizer, a common bleach refugee target. It is an allowlist
sanitizer, but it declined bleach feature parity: no escape-instead-of-strip mode, no attribute-rewriting callable, and
no linkifier.

***************
 Why turbohtml
***************

``turbohtml.sanitizer`` stays in the same performance tier as the Rust binding while restoring the features nh3 dropped:
an escape mode, a value-rewriting ``attribute_filter``, and a companion linkifier, all fully type annotated behind a
frozen :class:`~turbohtml.sanitizer.Policy`. It also leads nh3 on the benchmark:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - sanitize
      - turbohtml
      - nh3
      - speed-up
    - - comment (1 link, 1 script)
      - 1.5 µs
      - 5.5 µs
      - 3.7x
    - - post (4 KiB)
      - 42.1 µs
      - 120.1 µs
      - 2.9x

*************
 The renames
*************

.. code-block:: python

    # nh3
    import nh3

    nh3.clean(text, tags={"a"}, attributes={"a": {"href"}})

    # turbohtml
    from turbohtml.sanitizer import sanitize, Policy

    sanitize(text, Policy(tags=frozenset({"a"}), attributes={"a": frozenset({"href"})}))

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - nh3
      - turbohtml
    - - ``nh3.clean(text, ...)``
      - :func:`turbohtml.sanitizer.sanitize` with a :class:`~turbohtml.sanitizer.Policy`
    - - ``tags=``, ``attributes=``
      - ``Policy.tags``, ``Policy.attributes``
    - - ``link_rel=``
      - ``Policy.add_link_rel``
    - - ``url_schemes=``
      - ``Policy.url_schemes``
    - - ``attribute_filter=``
      - ``Policy.attribute_filter`` (may rewrite a value, not only drop it)
    - - ``set_tag_attribute_values=``
      - ``Policy.set_attributes``
    - - (drops disallowed tags)
      - :class:`~turbohtml.sanitizer.OnDisallowed` (``ESCAPE`` by default; ``STRIP`` / ``REMOVE`` for nh3-style
        dropping)
