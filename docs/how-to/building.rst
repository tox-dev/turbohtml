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

*********************************
 Build a whole page with a shell
*********************************

``E`` builds a fragment; :func:`turbohtml.build.document` builds the whole page. It emits ``<!DOCTYPE html>`` and the
``<html>``/``<head>``/``<body>`` shell around the content you pass, leading the head with a ``<meta charset>`` and an
optional ``<title>``, and hands back a :class:`~turbohtml.Document`:

.. testcode::

    from turbohtml.build import E, document

    page = document(
        title="Release notes",
        lang="en",
        body=[E.h1("Release notes"), E.p("Native page-shell builder.")],
    )
    print(page.serialize())

.. testoutput::

    <!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Release notes</title></head><body><h1>Release notes</h1><p>Native page-shell builder.</p></body></html>

Pass ``charset=None`` to drop the meta (say, when an HTTP header sets it), ``title=None`` to omit the title, and extra
``head`` content lands after the meta and title.
