Limit the nested ``<time>`` and date-marked elements whose text :func:`~turbohtml.extract.dates` scans to eight levels,
which keeps the scanning time proportional to the page size on deeply nested markup. Deeper elements are dated from
their ``datetime`` or ``title`` attribute only; their ``signal`` can change from ``time`` to ``text``, or the date can
be ``None`` with ``extensive_search=False``.
