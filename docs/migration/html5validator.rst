#####################
 From html5validator
#####################

.. package-meta:: html5validator svenkreiss/html5validator

`html5validator <https://github.com/svenkreiss/html5validator>`_ wraps the Nu Html Checker (``vnu.jar``), the reference
HTML5 conformance validator behind `validator.nu <https://validator.nu>`_, as a Python command and a small ``Validator``
class. It is the standard way to run conformance checks from Python -- in a CI step or a pre-commit hook -- and it flags
the authoring mistakes that parse cleanly but violate the standard: a missing ``img`` alt, obsolete markup, a duplicate
id, an undefined ARIA role. It shells out to a bundled JVM for every run.

turbohtml covers the same checks natively in :mod:`turbohtml.conformance`. :func:`~turbohtml.conformance.check_html`
parses a string and returns a :class:`~turbohtml.conformance.ConformanceReport` -- a ``valid`` verdict plus every
:class:`~turbohtml.conformance.ConformanceMessage` classified by severity -- with no subprocess, no JVM, and no jar to
install, so a check runs in microseconds instead of a JVM launch.

*****************************
 turbohtml vs html5validator
*****************************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - html5validator
    - - Engine
      - Native C, in-process
      - ``vnu.jar`` over a JVM subprocess
    - - Rule coverage
      - Alt text, obsolete markup, duplicate ids, ARIA roles, heading and section structure, document title and language
      - The full Nu Html Checker rule set
    - - Result
      - Severity-classified messages with a stable ``code`` and source position
      - vnu text, JSON, or gnu output parsed from the subprocess
    - - Runtime dependency
      - None
      - A Java runtime plus the bundled jar
    - - Performance
      - Microseconds per document, no process spawn
      - Seconds per run, dominated by JVM startup
    - - Typing
      - Fully annotated, ``py.typed``
      - Untyped

The rule sets differ in breadth: vnu enforces the whole standard, while turbohtml checks the high-value authoring rules
listed above. For those rules the two agree on the verdict, which the differential suite pins by cross-checking
turbohtml against ``vnu.jar`` itself.

*****************
 Feature overlap
*****************

- ``html5validator.Validator().validate([path])``, which returns a count of errors, maps to ``not
  check_html(markup).valid`` -- a boolean verdict from the report's error findings.
- vnu's per-message ``type``/``subType`` (``error``, ``info``/``warning``) maps to :attr:`ConformanceMessage.severity
  <turbohtml.conformance.ConformanceMessage.severity>` (``"error"``, ``"warning"``, ``"info"``), and its
  ``lastLine``/``firstColumn`` to the message ``line`` and ``column``.
- Reading vnu's JSON output to gate a build maps to the report's
  :attr:`~turbohtml.conformance.ConformanceReport.errors`, :attr:`~turbohtml.conformance.ConformanceReport.warnings`,
  and :attr:`~turbohtml.conformance.ConformanceReport.infos` views.
- Validating an already-parsed tree maps to :func:`turbohtml.conformance.check`, which skips the reparse.

.. testcode::

    from turbohtml.conformance import check_html

    report = check_html(
        "<!DOCTYPE html><html lang=en><head><title>Page</title></head>"
        "<body><img src=logo.png><font>old</font></body></html>"
    )
    print(report.valid)
    for message in report.errors:
        print(message.code, message.line, message.message)

.. testoutput::

    False
    img-missing-alt 1 img element has no alt attribute
    obsolete-element 1 the font element is obsolete

.. bench-table::
    :file: bench/html5validator.json
