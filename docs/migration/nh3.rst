##########
 From nh3
##########

`nh3 <https://github.com/messense/nh3>`_, the Rust ammonia binding, is the other bleach-refugee target, but it declined
bleach feature parity: it has no escape-instead-of-strip mode, no attribute-rewriting callable, and no linkifier.
``turbohtml.sanitizer`` covers those while staying in the same performance tier:

.. code-block:: python

    # nh3
    import nh3

    nh3.clean(text, tags={"a"}, attributes={"a": {"href"}})

    # turbohtml
    from turbohtml.sanitizer import sanitize, Policy

    sanitize(text, Policy(tags=frozenset({"a"}), attributes={"a": frozenset({"href"})}))

nh3's ``link_rel`` maps to ``Policy.add_link_rel``, its ``url_schemes`` to ``url_schemes``, and its ``attribute_filter``
to ``attribute_filter`` (turbohtml's may rewrite a value, not only drop it). Its ``set_tag_attribute_values`` (force an
attribute onto matching tags) maps to ``Policy.set_attributes``. turbohtml escapes disallowed tags by default
(``OnDisallowed.ESCAPE``), the mode ammonia blocked upstream; pass ``OnDisallowed.STRIP`` or ``OnDisallowed.REMOVE`` for
nh3-style dropping.
