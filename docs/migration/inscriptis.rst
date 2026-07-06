#################
 From inscriptis
#################

.. package-meta:: inscriptis weblyzard/inscriptis

`inscriptis <https://github.com/weblyzard/inscriptis>`_ converts HTML to *layout-aware* plain text. Instead of stripping
tags, it drives a CSS model so the output keeps the page's visual structure: tables become aligned columns, block
elements get blank lines and indentation, and list items keep their bullets. On top of that it can emit *annotations* —
labeled ``(start, end, label)`` spans over the rendered text, driven by a per-tag rule map — which makes it a common
preprocessing step for information-extraction and NLP pipelines that need clean, human-readable text with provenance. It
parses with lxml and evaluates its CSS model in Python.

turbohtml covers the same ground with :meth:`~turbohtml.Node.to_text` (the layout-aware renderer) and
:meth:`~turbohtml.Node.to_annotated_text` (the annotated variant), both configured through the frozen
:class:`~turbohtml.PlainText` dataclass. The whole layout pass runs in the C extension over a tree turbohtml already
owns, so text extraction is one native walk with no lxml dependency.

*************************
 turbohtml vs inscriptis
*************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - inscriptis
    - - Scope
      - Full WHATWG parser, mutable DOM, selectors, serializers; text rendering is one output mode
      - HTML-to-text conversion only (plus annotations)
    - - Feature breadth
      - Layout text, annotations, plus Markdown, HTML serialization, CSS/XPath selectors, encoding detection
      - Layout text and annotations, with a fully user-editable CSS profile
    - - Performance
      - Native C layout walk, roughly 75x to 100x faster (see below)
      - lxml tree plus a Python CSS model
    - - Typing
      - Fully type annotated, ships ``py.typed`` stubs
      - Type hints on the public API
    - - Dependencies
      - No runtime dependencies (self-contained C extension)
      - Depends on lxml / libxml2
    - - Maintenance
      - Actively developed alongside the parser core
      - Actively maintained by weblyzard

Feature overlap
===============

The layout-text and annotation surface ports one-to-one:

- ``get_text(html)`` -> :meth:`turbohtml.parse(html).to_text() <turbohtml.Node.to_text>`.
- ``get_annotated_text(html, ParserConfig(annotation_rules=...))`` ->
  :meth:`turbohtml.parse(html).to_annotated_text(rules) <turbohtml.Node.to_annotated_text>`, returning the rendered text
  plus a list of ``(start, end, label)`` triples over its code-point offsets.
- ``ParserConfig`` rendering options -> :class:`~turbohtml.PlainText` fields (``display_links`` -> ``links``,
  ``display_images`` -> ``images``, ``table_cell_separator`` -> ``table_cell_separator``, the CSS profile -> ``layout``,
  the list bullet -> ``bullet``).
- Annotation rule keys use the same grammar: a bare tag (``"h1"``), ``tag#attr`` to require an attribute,
  ``tag#attr=value`` to match one whitespace-separated token, and the tag-less ``#attr`` / ``#attr=value`` forms to
  match across any tag. The value is the list of labels to attach.
- The annotation *output processors* port as pure functions over the ``(text, spans)`` pair:
  :func:`turbohtml.annotation_surface` groups matched substrings by label (inscriptis's ``SurfaceExtractor``), and
  :func:`turbohtml.annotation_tags` weaves the spans back into the text as ``<label>...</label>`` markup, innermost span
  closing first (inscriptis's XML / inline-tag processor).

What turbohtml adds
===================

- **A real DOM.** ``to_text`` is one method on a parsed tree, so the same document can be queried with CSS/XPath
  selectors, mutated, and re-serialized. inscriptis only ever hands back a string.
- **Word wrapping.** :class:`~turbohtml.PlainText` takes ``width`` to reflow prose at a column; inscriptis leaves
  wrapping to the caller.
- **Other output modes** on the same node: :meth:`~turbohtml.Node.to_markdown`, :meth:`~turbohtml.Node.serialize`, and
  the raw :attr:`~turbohtml.Node.text` concatenation with no layout at all.
- **No lxml dependency** and a native C layout pass, which is where the speedup comes from.
- **A shell entry point.** ``python -m turbohtml to-text`` (installed as the ``turbohtml`` console script) reads a file
  or stdin, covering inscriptis's ``inscript`` command-line tool.

What inscriptis has that turbohtml does not
===========================================

- **A user-editable CSS model.** inscriptis lets you pass a custom CSS profile (per-tag ``display``, ``margin``,
  ``padding``, ``white-space`` rules) through ``ParserConfig(css=...)``. turbohtml exposes only the fixed
  ``layout="strict"`` / ``layout="extended"`` profiles, not per-tag overrides. Workaround: pick the closest profile and
  post-process, or mutate the tree before ``to_text``.
- **Anchor and caption toggles.** inscriptis's ``display_anchors`` (render ``id`` targets) and ``deduplicate_captions``
  have no direct :class:`~turbohtml.PlainText` equivalent. Workaround: strip or dedupe the relevant nodes on the tree
  before rendering.

Performance
===========

Doing the whole layout natively makes text extraction roughly 75x to 100x faster:

.. bench-table::
    :file: bench/inscriptis.json

****************
 How to migrate
****************

Swap the module-level ``get_text`` for a parse-then-render call:

.. code-block:: python

    # inscriptis
    from inscriptis import get_text

    get_text(html)

    # turbohtml
    import turbohtml

    turbohtml.parse(html).to_text()

The ``ParserConfig`` rendering options map onto :class:`~turbohtml.PlainText` fields passed to
:meth:`~turbohtml.Node.to_text`:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `inscriptis <https://github.com/weblyzard/inscriptis>`__ ``ParserConfig``
      - turbohtml :class:`~turbohtml.PlainText`
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

Layout text renders tables as aligned columns, the same as inscriptis:

.. testcode::

    html = "<h1>Sales</h1><table><tr><th>Region</th><th>Total</th></tr><tr><td>North</td><td>120</td></tr></table>"
    print(parse(html).to_text())

.. testoutput::

    Sales

    Region  Total
    North   120

Annotations port the same way. ``get_annotated_text`` becomes :meth:`~turbohtml.Node.to_annotated_text`, which takes the
rule map directly and accepts every ``to_text`` option as well:

.. code-block:: python

    # inscriptis
    from inscriptis import get_annotated_text, ParserConfig

    rules = {"h1": ["heading"], "b": ["emphasis"]}
    get_annotated_text(html, ParserConfig(annotation_rules=rules))

    # turbohtml
    turbohtml.parse(html).to_annotated_text(rules)

.. testcode::

    text, labels = parse("<h1>Title</h1><p>Some <b>bold</b> words.</p>").to_annotated_text({
        "h1": ["heading"],
        "b": ["emphasis"],
    })
    print(text)
    print([(label, text[start:end]) for start, end, label in labels])

.. testoutput::

    Title

    Some bold words.
    [('heading', 'Title'), ('emphasis', 'bold')]

The annotation output processors map onto two pure functions over the ``(text, spans)`` pair:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `inscriptis <https://github.com/weblyzard/inscriptis>`__ output processor
      - turbohtml
    - - ``SurfaceExtractor`` (``surface`` annotation processor)
      - :func:`turbohtml.annotation_surface`
    - - the XML / inline-tag annotation processor
      - :func:`turbohtml.annotation_tags`

.. testcode::

    from turbohtml import annotation_surface, annotation_tags, parse

    text, spans = parse("<h1>Q3</h1><p>Up <b>12%</b> on the year.</p>").to_annotated_text({
        "h1": ["heading"],
        "b": ["metric"],
    })
    print(annotation_surface(text, spans))
    print(annotation_tags(text, spans))

.. testoutput::

    {'heading': ['Q3'], 'metric': ['12%']}
    <heading>Q3</heading>

    Up <metric>12%</metric> on the year.

**********************
 Gotchas and pitfalls
**********************

- Links are hidden by default (matching inscriptis); pass ``PlainText(links="inline")`` for ``text (url)`` or
  ``PlainText(links="footnote")`` for numbered references collected at the end.
- :meth:`~turbohtml.Node.to_text` renders structure, not styling: there is no bold or color, and headings are plain
  text. For the raw concatenation with no layout at all, read :attr:`~turbohtml.Node.text`.
- Annotation offsets count code points into the returned string; a table cell is labeled at its position in the laid-out
  grid, so the span covers the cell's column-aligned text rather than its source order.
- turbohtml's ``layout`` is a two-value profile (``"strict"`` / ``"extended"``), not the full editable CSS model
  inscriptis exposes; if you relied on a custom CSS profile, reshape the tree before rendering instead.
