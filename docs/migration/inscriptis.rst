#################
 From inscriptis
#################

`inscriptis <https://github.com/weblyzard/inscriptis>`_ renders HTML to *layout-aware* plain text: it keeps the visual
structure, most visibly by laying tables out as aligned columns. :meth:`~turbohtml.Node.to_text` is the same idea in C,
replacing ``get_text``:

.. code-block:: python

    # inscriptis
    from inscriptis import get_text

    get_text(html)

    # turbohtml
    import turbohtml

    turbohtml.parse(html).to_text()

.. testcode::

    html = "<h1>Sales</h1><table><tr><th>Region</th><th>Total</th></tr><tr><td>North</td><td>120</td></tr></table>"
    print(parse(html).to_text())

.. testoutput::

    Sales

    Region  Total
    North   120

The ``ParserConfig`` options map as:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - inscriptis
      - turbohtml ``to_text(...)``
    - - ``display_links``
      - ``links`` (``"none"``/``"inline"``/``"footnote"``)
    - - ``display_images``
      - ``images``
    - - ``table_cell_separator``
      - ``table_cell_separator``
    - - the ``strict`` / ``relaxed`` CSS profile
      - ``layout`` (``"strict"``/``"extended"``)
    - - the list bullet
      - ``bullet``
    - - (no equivalent)
      - ``width`` adds word wrapping, which inscriptis leaves to the caller

inscriptis can also tag the rendered text with labeled spans through ``get_annotated_text`` and an ``annotation_rules``
mapping. :meth:`~turbohtml.Node.to_annotated_text` is the same call: it returns the rendered text together with a list
of ``(start, end, label)`` triples over its code-point offsets, taking every ``to_text`` option as well.

.. code-block:: python

    # inscriptis
    from inscriptis import get_annotated_text, ParserConfig

    rules = {"h1": ["heading"], "b": ["emphasis"]}
    get_annotated_text(html, ParserConfig(annotation_rules=rules))

    # turbohtml
    turbohtml.parse(html).to_annotated_text(rules)

.. testcode::

    text, labels = parse("<h1>Title</h1><p>Some <b>bold</b> words.</p>").to_annotated_text(
        {"h1": ["heading"], "b": ["emphasis"]}
    )
    print(text)
    print([(label, text[start:end]) for start, end, label in labels])

.. testoutput::

    Title

    Some bold words.
    [('heading', 'Title'), ('emphasis', 'bold')]

Rule keys follow inscriptis: a bare tag (``"h1"``), a ``tag#attr`` to require an attribute, a ``tag#attr=value`` to
match one whitespace-separated token of it, and the tag-less ``#attr`` / ``#attr=value`` forms to match across any tag.
The value is the list of labels to attach.

inscriptis also ships *annotation output processors* that render those spans. Both are ported as pure functions over the
``(text, spans)`` pair: :func:`turbohtml.annotation_surface` is the surface-form extractor (group the matched substrings
by label) and :func:`turbohtml.annotation_tags` is the inline-tagged (XML) exporter (weave the spans back into the text
as ``<label>...</label>`` markup, innermost span closing first).

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - inscriptis output processor
      - turbohtml
    - - ``SurfaceExtractor`` (``surface`` annotation processor)
      - :func:`turbohtml.annotation_surface`
    - - the XML / inline-tag annotation processor
      - :func:`turbohtml.annotation_tags`

.. testcode::

    from turbohtml import annotation_surface, annotation_tags, parse

    text, spans = parse("<h1>Q3</h1><p>Up <b>12%</b> on the year.</p>").to_annotated_text(
        {"h1": ["heading"], "b": ["metric"]}
    )
    print(annotation_surface(text, spans))
    print(annotation_tags(text, spans))

.. testoutput::

    {'heading': ['Q3'], 'metric': ['12%']}
    <heading>Q3</heading>

    Up <metric>12%</metric> on the year.

**********
 Pitfalls
**********

- Links are hidden by default (matching inscriptis); pass ``links="inline"`` for ``text (url)`` or ``links="footnote"``
  for numbered references collected at the end.
- ``to_text`` renders structure, not styling: there is no bold or color, and headings are plain text. For the raw
  concatenation with no layout at all, read :attr:`~turbohtml.Node.text`.
- Annotation offsets count code points into the returned string; a table cell is labeled at its position in the laid-out
  grid, so the span covers the cell's column-aligned text rather than the source order.
- :meth:`~turbohtml.Node.to_annotated_text` returns the ``(text, spans)`` list directly; render it with
  :func:`turbohtml.annotation_surface` or :func:`turbohtml.annotation_tags`, the ports of inscriptis's surface-form and
  inline-tagged output processors, or format the spans yourself.
