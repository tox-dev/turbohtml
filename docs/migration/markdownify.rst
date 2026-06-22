##################
 From markdownify
##################

.. image:: https://static.pepy.tech/badge/markdownify
    :alt: markdownify downloads
    :target: https://pepy.tech/project/markdownify

`markdownify <https://github.com/matthewwithanm/python-markdownify>`_ converts HTML to Markdown by walking a
BeautifulSoup tree, with a per-tag ``convert_<tag>`` override system for customizing the output.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Node.to_markdown` replaces a ``markdownify`` call with one method on the parsed tree. It is fully type
annotated, exposes one keyword per concept, and runs the conversion in C off the WHATWG tree instead of a second Python
walk over a BeautifulSoup tree, so it converts a page two orders of magnitude faster:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - to Markdown
      - turbohtml
      - markdownify
    - - article (2 KiB)
      - 12 µs
      - 1267 µs
    - - list (4 KiB)
      - 21 µs
      - 2453 µs
    - - table (4 KiB)
      - 27 µs
      - 2948 µs
    - - configured (4 KiB)
      - 28 µs
      - 2726 µs

*************
 The renames
*************

.. code-block:: python

    # markdownify
    from markdownify import markdownify

    markdownify(text)

    # turbohtml
    import turbohtml

    turbohtml.parse(text).to_markdown()

.. testcode::

    print(parse("<h1>Title</h1><p>Some <b>bold</b> text.</p>").to_markdown())

.. testoutput::

    # Title

    Some **bold** text.

The defaults emit opinionated GitHub-Flavored Markdown, and keyword options cover markdownify's surface with one name
per concept:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - markdownify
      - turbohtml :meth:`~turbohtml.Node.to_markdown`
    - - ``heading_style`` (``atx``/``atx_closed``/``underlined``)
      - ``heading_style`` (``"atx"``/``"atx_closed"``/``"setext"``)
    - - ``bullets``
      - ``bullets``
    - - ``strong_em_symbol``
      - ``strong`` and ``emphasis`` (independent, so a superset)
    - - ``sub_symbol``, ``sup_symbol``
      - ``sub_symbol``, ``sup_symbol``
    - - ``escape_asterisks``, ``escape_underscores``
      - ``escape_asterisks``, ``escape_underscores``
    - - ``escape_misc``
      - ``escape_mode="all"``
    - - ``autolinks``
      - ``autolink``
    - - ``default_title``
      - ``link_title``
    - - ``table_infer_header``
      - ``table_header="first"`` (the default) vs ``"none"``
    - - ``newline_style`` (``spaces``/``backslash``)
      - ``line_break`` (``"spaces"``/``"backslash"``)
    - - ``strip_document``
      - ``document_strip`` (``"strip"``/``"lstrip"``/``"rstrip"``/``"none"``)
    - - ``code_language``
      - ``code_language``
    - - ``strip``, ``convert``
      - ``strip``, ``convert`` (mutually exclusive tag filters)
    - - ``convert_<tag>`` overrides
      - ``converters`` argument

**********
 Pitfalls
**********

- The bold and italic markers are independent (``strong`` and ``emphasis``), where markdownify derives both from one
  ``strong_em_symbol``; set both to reproduce its behavior.
- :meth:`~turbohtml.Node.to_markdown` is a method on any node, so convert a subtree by calling it on the element you
  selected (``doc.find("article").to_markdown()``) instead of slicing the HTML string first.
- markdownify's parser-selection options (``bs4_options``) are dropped, since turbohtml always runs the WHATWG
  algorithm.
- ``base_url`` does simple prefixing rather than full RFC-3986 URL resolution.
