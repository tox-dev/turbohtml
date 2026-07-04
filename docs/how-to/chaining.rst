############################
 Chain queries like pyquery
############################

Wrap a set of elements in :class:`turbohtml.query.Query` and compose traversals and edits, for code migrating off
pyquery's jQuery-style chaining.

For code migrating off pyquery's jQuery-style chaining, :class:`turbohtml.query.Query` wraps a set of elements and every
traversal and mutation method returns a new wrapper, so calls compose. The method names are turbohtml's own (so
``add_class`` rather than ``addClass``), but the structure carries over:

.. testcode::

    from turbohtml.query import Query

    page = Query("<ul><li class=x>a</li><li>b</li><li class=x>c</li></ul>")
    print(page("li").filter(".x").eq(0).add_class("first").attr("class"))
    print(page("li").text())

.. testoutput::

    x first
    a b c
