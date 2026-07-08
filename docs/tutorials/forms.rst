####################
 Working with forms
####################

A form is a small database embedded in a page: named controls, each with a value, some checked and some not. This
tutorial reads those values with form semantics, sets one, and collects the whole form the way a browser submit would.

Parse a form with a text field, a select, and a checkbox:

.. testcode::

    import turbohtml

    form = turbohtml.parse(
        "<form><input name=email value=a@b.c>"
        "<select name=plan><option value=free>Free<option value=pro selected>Pro</select>"
        "<input name=terms type=checkbox value=yes></form>"
    ).find("form")

****************
 Read a control
****************

:attr:`~turbohtml.Element.field_value` gives a control's value with form semantics: a text input's value, or the
selected option of a ``select`` (a ``list`` for a ``multiple`` one). You do not reach into the ``<option>`` yourself:

.. testcode::

    print(form.find("input", attrs={"name": "email"}).field_value)
    print(form.find("select").field_value)

.. testoutput::

    a@b.c
    pro

***************
 Set a control
***************

Assigning to :attr:`~turbohtml.Element.field_value` writes it back, and :attr:`~turbohtml.Element.checked` reads or sets
a checkbox or radio. Tick the terms box:

.. testcode::

    form.find("input", attrs={"name": "terms"}).checked = True
    print(form.find("input", attrs={"name": "terms"}).checked)

.. testoutput::

    True

**********************
 Collect a submission
**********************

:meth:`~turbohtml.Element.form_data` returns the successful controls as ``(name, value)`` pairs in document order,
following the WHATWG submission rules -- unnamed, disabled, and unchecked controls are skipped. Now that the checkbox is
ticked, it contributes; feed the pairs straight to :func:`urllib.parse.urlencode`:

.. testcode::

    from urllib.parse import urlencode

    print(form.form_data())
    print(urlencode(form.form_data()))

.. testoutput::

    [('email', 'a@b.c'), ('plan', 'pro'), ('terms', 'yes')]
    email=a%40b.c&plan=pro&terms=yes

That is the round trip: read the fields, change one, and serialize the form as a browser would submit it. Next,
:doc:`cleaning` sanitizes untrusted markup and shrinks it.
