#############################
 Building and editing a tree
#############################

Everything so far read a document that already existed. You can also build one. Construct nodes from their classes and
assemble them with :meth:`~turbohtml.Element.append`; the ``text`` setter fills an element with a single text child:

.. testcode::

    import turbohtml
    from turbohtml import Element, Comment

    article = turbohtml.Element("article", {"class": "post"})
    title = turbohtml.Element("h1")
    title.text = "Tea"
    article.append(title)
    article.append(Comment("draft"))
    print(article.html)

.. testoutput::

    <article class="post"><h1>Tea</h1><!--draft--></article>

A list value for a token-list attribute (``class``, ``rel``, ...) joins on a space, and ``None`` (or ``""``) sets an
empty attribute, which reads back as the empty string:

.. testcode::

    print(turbohtml.Element("input", {"class": ["a", "b"], "disabled": None}).html)

.. testoutput::

    <input class="a b" disabled="">

Editing a parsed tree uses the BeautifulSoup vocabulary (``insert_before``, ``replace_with``, ``wrap``, ``unwrap``,
``decompose``), and ``element.attrs`` is a live mapping you assign to. A node already in a tree moves; a node from
another tree is adopted by copy:

.. testcode::

    doc = turbohtml.parse("<p>keep <b>bold</b> <span>drop</span></p>")
    print(doc.find("b").unwrap())
    doc.find("span").decompose()
    doc.find("p").attrs["class"] = "lead"
    print(doc.find("p").html)

.. testoutput::

    Element('b')
    <p class="lead">keep bold </p>

Duplicate a subtree with :func:`python:copy.deepcopy` (or :mod:`python:pickle`); the clone is a standalone tree you can
edit without touching the original:

.. testcode::

    import copy

    clone = copy.deepcopy(article)
    clone.append(turbohtml.Element("footer"))
    print(clone.html == article.html)

.. testoutput::

    False

Reshaping is not limited to trees you built. When you clean untrusted markup, the sanitizer can rename tags in the same
pass: a :class:`~turbohtml.clean.Policy` with ``transform_tags`` rewrites a source tag to a target (a
:class:`~turbohtml.clean.Transform` also adds attributes). The rename runs before the allowlist, so the result is
re-checked -- modernizing legacy tags while the safety baseline still applies:

.. testcode::

    from turbohtml.clean import sanitize, Policy, Transform

    policy = Policy(
        tags=frozenset({"strong", "em", "div"}),
        attributes={"div": frozenset({"class"})},
        transform_tags={"b": "strong", "i": "em", "center": Transform("div", {"class": "center"})},
    )
    print(sanitize("<center><b>Old</b> <i>markup</i></center>", policy))

.. testoutput::

    <div class="center"><strong>Old</strong> <em>markup</em></div>

The same pass can defuse DOM clobbering. An ``id`` or ``name`` whose value matches a built-in property
(``form.attributes``, ``document.body``) shadows it through named access, and the allowlist keeps such an attribute
because it is otherwise ordinary. Turn on ``isolate_named_props`` to prefix every kept ``id`` and ``name`` value with
``user-content-``, moving it out of the property namespace:

.. testcode::

    policy = Policy(
        tags=frozenset({"form", "input"}),
        attributes={"form": frozenset({"id"}), "input": frozenset({"name"})},
        isolate_named_props=True,
    )
    print(sanitize('<form id="settings"><input name="attributes"></form>', policy))

.. testoutput::

    <form id="user-content-settings"><input name="user-content-attributes"></form>

When you serialize, set an ``Html`` config's ``layout`` to a :class:`~turbohtml.Minify` to shrink the output without
changing what it means: it folds insignificant whitespace, omits optional tags, unquotes safe attributes, and strips
comments, and the result reparses to the same tree. Here the ``</li>`` tags stay because real whitespace separates the
items:

.. testcode::

    from turbohtml import Html, Minify

    page = turbohtml.parse("<ul>\n  <li>one</li>\n  <li>two</li>\n</ul>")
    print(page.find("ul").serialize(Html(layout=Minify())))

.. testoutput::

    <ul> <li>one</li> <li>two</li> </ul>

With a tree you can build and reshape, continue to :doc:`exporting` to turn it back into text.
