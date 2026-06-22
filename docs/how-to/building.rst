###################
 Build HTML with E
###################

*******************************
 Build an HTML fragment with E
*******************************

When you generate HTML rather than parse it, :data:`turbohtml.build.E` builds the tree without the ``Element`` and
``append`` boilerplate. A leading mapping is the attributes, and each remaining argument is a child node or a string
that becomes text:

.. testcode::

    from turbohtml.build import E

    article = E.article(
        {"class": "post"},
        E.h1("Release notes"),
        E.ul(E.li("faster serialize"), E.li("new builder")),
    )
    print(article.serialize())

.. testoutput::

    <article class="post"><h1>Release notes</h1><ul><li>faster serialize</li><li>new builder</li></ul></article>

The result is an ordinary :class:`~turbohtml.Element`, so you can keep editing or querying it before serializing -- the
builder only saves the construction step.

*********************************************
 Build a tag that is not a Python identifier
*********************************************

``E.<tag>`` covers any tag spelled as an attribute, but a custom element or namespaced name needs the call form
``E("tag", ...)``. A list-valued attribute joins on a space, so a class list reads naturally:

.. testcode::

    from turbohtml.build import E

    print(E("my-card", {"class": ["card", "lg"]}, "hi").serialize())

.. testoutput::

    <my-card class="card lg">hi</my-card>
