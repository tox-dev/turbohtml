#############################
 Export a tree to plain text
#############################

Extract rendered text with :meth:`~turbohtml.Node.to_text`, and get the same text with span labels attached through
:meth:`~turbohtml.Node.to_annotated_text`.

**********************
 Export to plain text
**********************

:meth:`~turbohtml.Node.to_text` renders layout-aware plain text (the role `inscriptis
<https://github.com/weblyzard/inscriptis>`_ fills), keeping the visual structure rather than collapsing everything like
:attr:`~turbohtml.Node.text` does. Its most visible feature is laying tables out as aligned columns:

.. testcode::

    import turbohtml

    page = turbohtml.parse(
        "<h2>Stock</h2>"
        "<table><tr><th>Item</th><th>Qty</th></tr>"
        "<tr><td>Apples</td><td>3</td></tr><tr><td>Pears</td><td>40</td></tr></table>"
    )
    print(page.to_text())

.. testoutput::

    Stock

    Item    Qty
    Apples  3
    Pears   40

Links are hidden by default; pass a :class:`~turbohtml.PlainText` config to change that — ``PlainText(links="inline")``
appends ``text (url)``, ``links="footnote"`` numbers the references, ``images=True`` shows alt text, and ``width``
word-wraps:

.. testcode::

    from turbohtml import PlainText

    doc = turbohtml.parse('<p>See <a href="/docs">the docs</a> for more.</p>')
    print(doc.to_text(PlainText(links="inline")))

.. testoutput::

    See the docs (/docs) for more.

********************************
 Label spans of the export text
********************************

To pull labeled regions out of the rendered text (the role inscriptis fills with ``annotation_rules``), call
:meth:`~turbohtml.Node.to_annotated_text` with a rule mapping. It returns the same text :meth:`~turbohtml.Node.to_text`
would, plus a list of ``(start, end, label)`` triples whose offsets index into that text, and it accepts a
:class:`~turbohtml.PlainText` config as its second argument just like :meth:`~turbohtml.Node.to_text`:

.. testcode::

    import turbohtml

    text, labels = turbohtml.parse("<h1>Q3</h1><p>Up <b>12%</b> on the year.</p>").to_annotated_text({
        "h1": ["heading"],
        "b": ["metric"],
    })
    print(text)
    for start, end, label in labels:
        print(label, "->", repr(text[start:end]))

.. testoutput::

    Q3

    Up 12% on the year.
    heading -> 'Q3'
    metric -> '12%'

A rule key is a tag (``"b"``), a ``tag#attr`` requiring the attribute, a ``tag#attr=value`` matching one
whitespace-separated token, or the tag-less ``#attr`` / ``#attr=value`` to match across any tag; its value is the list
of labels to attach.
