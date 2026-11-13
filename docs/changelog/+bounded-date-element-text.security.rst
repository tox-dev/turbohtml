Limit the nested ``<time>`` and date-marked elements whose text :func:`~turbohtml.extract.dates` scans to eight levels.
Deeper elements are dated from their ``datetime`` or ``title`` attribute only, which keeps the scanning time
proportional to the page size on deeply nested markup.
