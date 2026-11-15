#################
 Work with forms
#################

Read and set form-control values with form semantics, and collect a form's data the way a browser submit would, through
:attr:`~turbohtml.Element.field_value` and the form helpers.

********************************
 Read and set form-field values
********************************

:attr:`~turbohtml.Element.field_value` is the control's value with form semantics: a textarea's text, an option's value,
or the selected option value(s) of a ``select`` (a ``list`` for a ``multiple`` select). Assigning writes it back, and
:attr:`~turbohtml.Element.checked` reads or sets a checkbox or radio (setting a radio to ``True`` clears the other
same-name radios in the form):

.. testcode::

    form = turbohtml.parse(
        "<form><input name=email value=a@b.c>"
        "<select name=plan><option value=free>Free<option value=pro selected>Pro</select>"
        "<input name=terms type=checkbox value=yes></form>"
    ).find("form")
    print(form.find("input", attrs={"name": "email"}).field_value)
    print(form.find("select").field_value)
    form.find("input", attrs={"name": "terms"}).checked = True

.. testoutput::

    a@b.c
    pro

**************************************
 Serialize a form to name/value pairs
**************************************

:meth:`~turbohtml.Element.form_data` returns the form's successful controls as ``(name, value)`` pairs in document
order, following the WHATWG submission rules: unnamed, disabled, button, and unchecked checkbox/radio controls are
skipped, controls inside a ``datalist`` are left out, and a ``select`` contributes one pair per selected option. An SVG
or MathML element named like a control or an option is not one, so it contributes nothing, while HTML controls inside it
still count. Each value is the one a browser submits: an input's ``value`` attribute after its type's sanitization
(newlines stripped from text, a ``url`` or ``email`` trimmed, an invalid ``number`` or date emptied, a ``range`` clamped
to its ``min``, ``max``, and ``step``), and a ``textarea``'s text with line breaks normalized to ``\n``. Pass the result
straight to :func:`urllib.parse.urlencode`:

.. testcode::

    from urllib.parse import urlencode

    print(form.form_data())
    print(urlencode(form.form_data()))

.. testoutput::

    [('email', 'a@b.c'), ('plan', 'pro'), ('terms', 'yes')]
    email=a%40b.c&plan=pro&terms=yes
