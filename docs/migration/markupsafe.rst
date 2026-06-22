#################
 From markupsafe
#################

``turbohtml.migration.markupsafe`` is a drop-in for `markupsafe <https://markupsafe.palletsprojects.com>`_'s public
surface, so a `Jinja2 <https://jinja.palletsprojects.com>`_, `WTForms <https://wtforms.readthedocs.io>`_, or `Werkzeug
<https://werkzeug.palletsprojects.com>`_ project changes only the import line:

.. code-block:: python

    # markupsafe
    from markupsafe import Markup, escape, escape_silent, soft_str, EscapeFormatter

    # turbohtml
    from turbohtml.migration.markupsafe import (
        Markup,
        escape,
        escape_silent,
        soft_str,
        EscapeFormatter,
    )

``escape`` returns a :class:`~turbohtml.migration.markupsafe.Markup` with the same numeric quote references markupsafe
emits, honors the ``__html__`` protocol, and leaves an existing ``Markup`` untouched. ``Markup`` overrides the full
:class:`str` method surface, so a value that flows through a template filter such as ``upper`` or ``replace`` stays a
``Markup`` and autoescaping does not escape it a second time. The operations that combine text (``+``, ``%``,
:meth:`~turbohtml.migration.markupsafe.Markup.format`, :meth:`~turbohtml.migration.markupsafe.Markup.join`, ``replace``,
...) escape their untrusted operands:

.. testcode::

    from turbohtml.migration.markupsafe import Markup, escape, escape_silent

    print(escape('<a href="x">Tom & Jerry</a>'))
    print(Markup("<b>{}</b>").format("<i>"))
    print(Markup("<b>safe</b>").upper())  # str methods keep the Markup, so it is not re-escaped
    print(escape_silent(None) == Markup(""))

.. testoutput::

    &lt;a href=&#34;x&#34;&gt;Tom &amp; Jerry&lt;/a&gt;
    <b>&lt;i&gt;</b>
    <B>SAFE</B>
    True

Two methods are upgrades rather than reimplementations: :meth:`~turbohtml.migration.markupsafe.Markup.striptags` and
:meth:`~turbohtml.migration.markupsafe.Markup.unescape` run on turbohtml's tokenizer and HTML5 reference resolution, so
they are faster and resolve references markupsafe's regex-based stripping can miss.

These differences from markupsafe do not affect migration: the escape runs in C, every ``Markup`` method runs faster
than markupsafe's, the ``soft_unicode`` alias that markupsafe 3.0 removed is absent here too, and turbohtml does not
register itself as ``markupsafe``, so adoption stays an explicit per-project import.
