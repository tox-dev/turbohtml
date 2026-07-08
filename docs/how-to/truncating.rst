##################################################
 Truncate HTML to a length, keeping tags balanced
##################################################

Cutting an HTML string at a character count with a slice breaks it: you can land in the middle of a tag or leave an
element open. Truncating the *tree* instead keeps the markup well-formed. Parse the HTML, walk it counting visible text,
and drop everything past the budget; because you edit nodes rather than a string, every tag you keep stays balanced.

The walk trims the text node that crosses the limit and removes whole subtrees after it with
:meth:`~turbohtml.Node.decompose`:

.. testcode::

    import turbohtml
    from turbohtml import Element, Text


    def truncate(html, limit):
        body = turbohtml.parse(html).find("body")
        _trim(body, limit)
        return "".join(child.serialize() for child in body)


    def _trim(node, budget):
        for child in list(node):
            if budget <= 0:
                child.decompose()
            elif isinstance(child, Text):
                if len(child.data) > budget:
                    child.data = child.data[:budget].rstrip() + "…"
                    budget = 0
                else:
                    budget -= len(child.data)
            elif isinstance(child, Element):
                budget = _trim(child, budget)
        return budget


    print(truncate("<div><p>Hello world, this is long.</p><p>Second para.</p></div>", 11))

.. testoutput::

    <div><p>Hello world…</p></div>

The count is over visible text, so tags do not eat the budget, and the second paragraph -- reached after the budget ran
out -- drops whole rather than emptied. An element trimmed mid-text keeps its wrapper:

.. testcode::

    print(truncate("<article><h2>Title</h2><p>Body text that is fairly long here.</p></article>", 9))

.. testoutput::

    <article><h2>Title</h2><p>Body…</p></article>

This is the Django ``Truncator.chars`` and ``TruncateHTMLParser`` behavior without the parser subclass: assign a
:attr:`Text.data <turbohtml.Text.data>` to rewrite a run of text, and call :meth:`~turbohtml.Node.decompose` to remove a
node and its subtree. To keep a whole region rather than a length, :doc:`pruning` trims to a selector instead.
