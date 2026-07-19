#############
 From pandas
#############

.. package-meta:: pandas pandas-dev/pandas

`pandas <https://pandas.pydata.org>`_ is the standard Python library for tabular data analysis: labeled columns, typed
arrays, group-by, joins, and file I/O across CSV, Parquet, SQL, and more. Web scrapers reach for exactly one corner of
it, `read_html <https://pandas.pydata.org/docs/reference/api/pandas.read_html.html>`_, which parses every ``<table>`` on
a page into a list of ``DataFrame`` objects, resolving ``rowspan``/``colspan`` and optionally inferring headers and
dtypes. To do that it needs a third-party HTML backend (``lxml``, ``html5lib``, or ``bs4``) plus pandas and NumPy
themselves.

turbohtml covers only that table-reading slice, and covers it without the dependency stack.
:meth:`~turbohtml.Element.rows` and :meth:`~turbohtml.Element.records` walk the cell grid in C and hand back plain lists
and dicts, so a scraper that only needs a grid of strings never imports NumPy. Everything else pandas does -- the
``DataFrame``, its dtypes, and its analytics -- stays outside turbohtml's scope; when you do want a frame, the records
are the exact shape ``pandas.DataFrame`` takes, so pandas stays an optional last step rather than a hard parse-time
dependency.

*********************
 turbohtml vs pandas
*********************

.. list-table::
    :header-rows: 1
    :widths: 18 41 41

    - - Dimension
      - turbohtml
      - pandas
    - - Scope
      - WHATWG HTML parse plus a C cell-grid reader that returns lists and dicts
      - Full tabular analytics library; ``read_html`` is one I/O helper over a pluggable HTML backend
    - - Feature breadth
      - ``rows``, ``records``, and ``tables`` returning strings; full parse, query, mutate, and serialize around them
      - Header inference, dtype coercion, ``NaN`` padding, URL fetch, backend flavors, link extraction, then the whole
        analytics API
    - - Performance
      - C cell-grid walk into plain containers; roughly 30 to 160 times faster than ``read_html`` on tables
      - Builds a NumPy-backed ``DataFrame`` per table, paying a fixed per-frame cost that dominates small tables
    - - Typing
      - Ships ``py.typed``; ``rows``/``records``/``tables`` return precise ``list`` types
      - Ships ``py.typed``; ``read_html`` is typed as ``list[DataFrame]``
    - - Dependencies
      - Zero runtime dependencies (self-contained C extension)
      - pandas and NumPy, plus ``lxml``/``html5lib``/``bs4`` for the HTML backend
    - - Maintenance
      - Actively developed, single-engine
      - Mature, very widely deployed, long release history

Feature overlap
===============

The shared surface is table reading; these port one-to-one (see the mapping under `How to migrate`_):

- Parsing table markup you already hold into a rectangular grid.
- Resolving ``rowspan`` and ``colspan`` into a filled grid of cells.
- Reading one table's body rows as a list of lists (:meth:`~turbohtml.Element.rows` for ``read_html(...)[0]``).
- Reading a table as header-keyed dicts (:meth:`~turbohtml.Element.records` for ``read_html(...,
  header=0)[0].to_dict("records")``).
- Reading every table in a document in one call (:meth:`~turbohtml.Node.tables` for the ``list[DataFrame]`` that
  ``read_html`` returns).

What turbohtml adds
===================

- **No NumPy or pandas import.** :meth:`~turbohtml.Element.rows` and :meth:`~turbohtml.Element.records` return built-in
  ``list``/``dict``, so reading a grid of strings pulls in nothing. pandas keeps ``DataFrame`` optional and last.
- **A C engine.** Parsing and the span-resolving grid walk both run in C, so small tables avoid the per-frame overhead
  that dominates ``read_html``.
- **A full document model.** The same parse gives you ``find``/``select``/XPath, mutation, and WHATWG serialization, so
  table extraction lives inside a real DOM rather than a one-shot ``io`` helper.
- **One deterministic algorithm.** turbohtml always runs the WHATWG parser; there is no backend flavor to pick or
  install, and no behavioral drift between ``lxml`` and ``html5lib`` modes.

What pandas has that turbohtml does not
=======================================

- **Dtype inference.** ``read_html`` coerces cells to ``int``/``float``/``NaN`` (and honors ``dtype``, ``converters``,
  ``thousands``, ``decimal``, ``parse_dates``). turbohtml cells are always ``str``. Workaround: pass
  :meth:`~turbohtml.Element.records` to ``pandas.DataFrame`` and let it infer, or convert columns yourself.
- **Header and index inference.** ``header=``, ``index_col=``, and multi-row headers reshape the frame. turbohtml treats
  the first row (typically the ``thead`` row) as the header for :meth:`~turbohtml.Element.records` and offers no index
  concept. Workaround: read :meth:`~turbohtml.Element.rows` and slice the header rows yourself.
- **Sparse span handling.** ``read_html`` can leave a spanned cell as a single value with ``NaN`` padding depending on
  options; turbohtml copies a spanned cell's text into every slot it covers. No option toggles this.
- **URL and file I/O with backend flavors.** ``read_html`` accepts a URL, path, or file object and picks an
  ``lxml``/``html5lib``/``bs4`` parser. turbohtml parses markup you already hold. Workaround: fetch with
  :mod:`urllib.request` (or ``requests``) and pass the bytes to :func:`turbohtml.parse`.
- **Table matching and filtering.** ``read_html`` filters tables by ``match=`` regex, ``attrs=``, and
  ``extract_links=``. turbohtml has no such filter on :meth:`~turbohtml.Node.tables`. Workaround:
  :meth:`~turbohtml.Node.find`/ :meth:`~turbohtml.Node.select` the ``<table>`` you want first, then call
  :meth:`~turbohtml.Element.rows`.

Performance
===========

Reading a table with :meth:`~turbohtml.Element.rows` and :meth:`~turbohtml.Element.records` against
``pandas.read_html``, both parsing the markup and resolving spans into a rectangular grid. turbohtml's C cell-grid walk
returns plain lists and dicts where ``read_html`` builds a ``DataFrame`` (importing NumPy), so it runs roughly 30 to 160
times faster: the gap is widest on small tables, where pandas pays a fixed per-frame cost, and narrows as the row count
grows. See :doc:`/development/performance` for the methodology.

.. bench-table::
    :file: bench/pandas.json

****************
 How to migrate
****************

Swap the ``read_html`` call for a parse plus a grid method. There is no backend flavor to pass, and no NumPy to install;
if you still want a ``DataFrame``, feed :meth:`~turbohtml.Element.records` to ``pandas.DataFrame`` as the last step:

.. code-block:: python

    # pandas
    import pandas as pd

    frame = pd.read_html(html, header=0)[0]
    records = frame.to_dict("records")

.. testcode::

    import turbohtml

    table = turbohtml.parse("<table><tr><th>name</th><th>qty</th></tr><tr><td>pen</td><td>3</td></tr></table>").find(
        "table"
    )
    records = table.records()
    print(records)
    # pandas.DataFrame(records) builds the frame, keeping pandas optional

.. testoutput::

    [{'name': 'pen', 'qty': '3'}]

API mapping
===========

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `pandas <https://pandas.pydata.org/>`__
      - turbohtml
    - - ``pandas.read_html(html)`` -> ``list[DataFrame]``
      - :meth:`turbohtml.parse(html).tables() <turbohtml.Node.tables>` -> ``list[list[list[str]]]``
    - - ``pandas.read_html(html)[0]``
      - :meth:`turbohtml.parse(html).find("table").rows() <turbohtml.Element.rows>`
    - - ``pandas.read_html(html, header=0)[0].to_dict("records")``
      - :meth:`turbohtml.parse(html).find("table").records() <turbohtml.Element.records>`
    - - ``DataFrame(pandas.read_html(html)[0])``
      - ``pandas.DataFrame(turbohtml.parse(html).find("table").records())``

:meth:`~turbohtml.Node.tables` returns every table in a subtree, each already shaped as :meth:`~turbohtml.Element.rows`,
so a whole document reads back without locating each ``<table>`` first:

.. testcode::

    document = turbohtml.parse("<table><tr><td>a</td></tr></table><table><tr><td>b</td><td>c</td></tr></table>")
    print(document.tables())

.. testoutput::

    [[['a']], [['b', 'c']]]

**********************
 Gotchas and pitfalls
**********************

- Cells are strings, always. turbohtml does not infer ``int``/``float``/``NaN`` dtypes the way pandas does; convert
  columns yourself, or let ``pandas.DataFrame`` do it after :meth:`~turbohtml.Element.records`.
- Spans are filled, not collapsed. A ``rowspan``/``colspan`` cell's text is copied into every slot it covers, where
  pandas can leave a single value and ``NaN`` padding depending on options.
- The header is the first row (typically the ``thead`` row); there is no ``header=``/``index_col=`` inference or a
  multi-row header. For anything fancier, read :meth:`~turbohtml.Element.rows` and slice it yourself.
- No I/O or flavors. ``read_html`` fetches URLs and picks an ``lxml``/``bs4`` parser; turbohtml parses markup you
  already hold with the one WHATWG algorithm, so fetch the page (e.g. with :mod:`urllib.request`) and pass the bytes to
  :func:`turbohtml.parse`.
