#############
 Conformance
#############

.. module:: turbohtml.conformance

:func:`check` runs the HTML5 authoring-conformance rules over a document parsed with :func:`turbohtml.parse` and returns
a :class:`ConformanceReport` -- a ``valid`` verdict plus every :class:`ConformanceMessage`, each with a stable ``code``,
a :data:`Severity`, a description, and a source line and column. The document is valid exactly when nothing is an error.
The whole walk runs in the C core; the Python layer only wraps the findings. :func:`check_html` parses a markup string
first.

.. autofunction:: check

.. autofunction:: check_html

.. autoclass:: ConformanceReport
    :members:

.. autoclass:: ConformanceMessage
    :members:

.. autodata:: Severity
