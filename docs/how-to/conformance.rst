##################################
 Check a document for conformance
##################################

.. currentmodule:: turbohtml.conformance

The WHATWG parser recovers from bad markup and records its :class:`~turbohtml.ParseError`\\s, but many authoring
mistakes leave a perfectly parseable tree: an image with no alt text, an obsolete ``<font>``, a duplicate id, an
undefined ARIA role. :func:`check_html` parses a markup string and runs the HTML5 authoring rules over it, returning a
:class:`ConformanceReport`.

******************
 Read the verdict
******************

A report is truthy when the document is valid -- when nothing it found is an error. Warnings and info notes are advice
and never change the verdict. Each :class:`ConformanceMessage` carries a stable ``code``, a ``severity``, a description,
and the source ``line`` and ``column`` of the node that triggered it:

.. testcode::

    from turbohtml.conformance import check_html

    report = check_html(
        "<html><head></head><body><img src=logo.png><nav role=navigation><a href=/>Home</a></nav></body></html>"
    )
    print(report.valid)
    for message in report.messages:
        print(message.severity, message.code, "--", message.message)

.. testoutput::

    False
    error img-missing-alt -- img element has no alt attribute
    warning aria-redundant-role -- role navigation duplicates the element's implicit role
    warning missing-lang -- html element has no lang attribute
    error missing-title -- document has no non-empty title element

*******************
 Split by severity
*******************

The report's :attr:`~ConformanceReport.errors`, :attr:`~ConformanceReport.warnings`, and
:attr:`~ConformanceReport.infos` views partition the messages, so a gate can fail the build on errors while only logging
the rest:

.. testcode::

    if not report:
        print("errors:", [message.code for message in report.errors])
        print("advice:", [message.code for message in report.warnings + report.infos])

.. testoutput::

    errors: ['img-missing-alt', 'missing-title']
    advice: ['aria-redundant-role', 'missing-lang']

******************************
 Check an already-parsed tree
******************************

When you have already parsed the document, pass it to :func:`check` to skip a reparse. Handing :func:`check` a subtree
runs only the per-element rules -- the document-level title and language rules apply to a whole
:class:`~turbohtml.Document`:

.. testcode::

    from turbohtml import parse
    from turbohtml.conformance import check

    document = parse("<html lang=en><head><title>Docs</title></head><body><h1>Hi</h1></body></html>")
    print(check(document).valid)

    fragment = parse("<section><img src=x></section>").find("section")
    print([message.code for message in check(fragment).messages])

.. testoutput::

    True
    ['section-no-heading', 'img-missing-alt']
