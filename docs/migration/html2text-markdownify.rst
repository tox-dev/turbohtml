##############################
 From html2text / markdownify
##############################

`html2text <https://github.com/Alir3z4/html2text>`_ and `markdownify
<https://github.com/matthewwithanm/python-markdownify>`_ both turn HTML into Markdown.
:meth:`~turbohtml.Node.to_markdown` replaces a call to either with one method on the parsed tree, and the conversion
runs in C rather than a Python walk over a second parser's tree:

.. code-block:: python

    # html2text
    import html2text

    html2text.html2text(text)

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

The defaults emit opinionated GitHub-Flavored Markdown, and keyword options cover the configuration surface of both
libraries with one name per concept. The markdownify options map as:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - markdownify
      - turbohtml ``to_markdown(...)``
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

The html2text options map as:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - html2text
      - turbohtml ``to_markdown(...)``
    - - ``ul_item_mark``
      - ``bullets``
    - - ``emphasis_mark``, ``strong_mark``
      - ``emphasis``, ``strong``
    - - ``ignore_emphasis``
      - ``ignore_emphasis``
    - - ``ignore_links``
      - ``ignore_links``
    - - ``skip_internal_links``
      - ``skip_internal_links``
    - - ``inline_links``
      - ``link_style`` (``"inline"``/``"reference"``)
    - - ``ignore_images``, ``images_to_alt``, ``images_as_html``, ``images_with_size``
      - ``image_mode`` (``"markdown"``/``"alt"``/``"ignore"``/``"html"``)
    - - ``default_image_alt``
      - ``default_image_alt``
    - - ``ignore_tables``, ``bypass_tables``
      - ``table_mode`` (``"markdown"``/``"strip"``/``"html"``)
    - - ``pad_tables``
      - ``pad_tables``
    - - ``body_width``, ``wrap_list_items``, ``wrap_links``
      - ``wrap_width``, ``wrap_list_items``, ``wrap_links``
    - - ``unicode_snob`` (and the ``UNIFIABLE`` table)
      - ``transliterate``
    - - ``mark_code``
      - ``mark_code``
    - - ``backquote_code_style``
      - ``code_block_style`` (``"fenced"``/``"indented"``)
    - - ``single_line_break``
      - ``block_spacing="single"``
    - - ``baseurl``
      - ``base_url``
    - - ``open_quote``, ``close_quote``
      - ``quote_open``, ``quote_close``
    - - ``escape_snob``
      - ``escape_mode="all"``
    - - ``google_doc``
      - ``google_doc``
    - - ``google_list_indent``
      - ``google_list_indent``
    - - ``hide_strikethrough``
      - ``hide_strikethrough``

``google_doc=True`` reads the inline-CSS styling a Google Docs HTML export carries: a ``font-weight`` of ``bold`` or
``700``--``900`` becomes ``strong``, ``font-style:italic`` becomes ``emphasis``, a ``Courier New``/``Consolas``
``font-family`` becomes an inline code span, ``list-style-type`` picks the list marker, and each ``google_list_indent``
pixels of ``margin-left`` add one list-nesting level. With ``hide_strikethrough=True`` a
``text-decoration:line-through`` drops the struck text.

.. testcode::

    export = '<p><span style="font-weight:700">Quarterly</span> revenue</p>'
    print(parse(export).to_markdown(google_doc=True))

.. testoutput::

    **Quarterly** revenue

**********
 Pitfalls
**********

- The bold and italic markers are independent (``strong`` and ``emphasis``), where markdownify derives both from one
  ``strong_em_symbol``; set both to reproduce its behavior.
- ``to_markdown`` is a method on any node, so convert a subtree by calling it on the element you selected
  (``doc.find("article").to_markdown()``) instead of slicing the HTML string first.
- Layout-aware plain text (the ``inscriptis`` role, ``to_text(layout=...)``) is a separate method; for the unstructured
  concatenation read :attr:`~turbohtml.Node.text`.

******************************
 Not yet ported / limitations
******************************

Custom per-tag conversion ships as the ``converters`` argument (markdownify's ``convert_<tag>`` overrides). What stays
out of scope is narrow:

- markdownify's parser-selection options (``bs4_options``) are dropped, since turbohtml always runs the WHATWG
  algorithm.
- ``base_url`` does simple prefixing rather than full RFC-3986 URL resolution.
