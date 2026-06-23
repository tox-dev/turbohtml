#####################
 From MechanicalSoup
#####################

.. image:: https://static.pepy.tech/badge/MechanicalSoup/month
    :alt: MechanicalSoup monthly downloads
    :target: https://pepy.tech/project/MechanicalSoup

`MechanicalSoup <https://github.com/MechanicalSoup/MechanicalSoup>`_ is a stateful browser: it fetches pages with
`requests <https://requests.readthedocs.io>`_, parses them with `BeautifulSoup
<https://www.crummy.com/software/BeautifulSoup/>`_, and fills and submits their forms. turbohtml replaces the parsing
and form-reading half; the HTTP session, page navigation, and submission (``StatefulBrowser``, ``open``,
``submit_selected``) stay out of scope -- fetch with `httpx <https://www.python-httpx.org>`_ or ``requests`` and post
the result yourself.

***************
 Why turbohtml
***************

Once the page is parsed, ``select_form`` becomes :meth:`~turbohtml.Node.select_one`, reading or setting a field through
``browser[name] = value`` becomes :attr:`~turbohtml.Element.field_value` and :attr:`~turbohtml.Element.checked`, and the
name/value pairs MechanicalSoup would submit come from :meth:`~turbohtml.Element.form_data` -- one typed method on the
parsed form instead of a stateful browser object.

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - MechanicalSoup
      - turbohtml
    - - ``browser.open(url)``, ``browser.get_current_page()``
      - fetch yourself, then ``turbohtml.parse(response.content)``
    - - ``browser.select_form("form")``
      - ``doc.select_one("form")``
    - - ``browser[name] = value``, ``form.set(name, value)``
      - ``field.field_value = value``
    - - reading ``form[name]``
      - ``field.field_value``
    - - ``form.check(name)`` / checkbox & radio state
      - ``field.checked = True``
    - - the data ``browser.submit_selected()`` posts
      - ``form.form_data()`` (hand to your HTTP client)

.. testcode::

    form = parse(
        "<form><input name=email value=a@b.c>"
        "<select name=plan><option value=free>Free<option value=pro selected>Pro</select>"
        "<input name=terms type=checkbox value=yes></form>"
    ).find("form")
    form.find("input", attrs={"name": "terms"}).checked = True
    print(form.find("input", attrs={"name": "email"}).field_value)
    print(form.form_data())

.. testoutput::

    a@b.c
    [('email', 'a@b.c'), ('plan', 'pro'), ('terms', 'yes')]

**********
 Pitfalls
**********

- :meth:`~turbohtml.Element.form_data` follows the WHATWG submission rules MechanicalSoup relied on BeautifulSoup plus
  its own walk to reproduce -- unnamed, disabled, button, and unchecked checkbox/radio controls are skipped, and a
  ``select`` contributes one pair per selected option.
- The result drops straight into :func:`urllib.parse.urlencode` or an ``httpx``/``requests`` ``data=`` argument.
- ``select_one`` returns the first matching form; on a page with several forms pass a more specific selector to reach
  the one you mean.
