#####################
 From MechanicalSoup
#####################

.. package-meta:: MechanicalSoup MechanicalSoup/MechanicalSoup

`MechanicalSoup <https://github.com/MechanicalSoup/MechanicalSoup>`_ is a stateful headless browser for Python. It wires
together three libraries: it fetches pages with `requests <https://requests.readthedocs.io>`_, parses them with
`BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`_, and adds a ``StatefulBrowser`` that remembers the
current page and form so you can follow links, fill in fields, and submit without hand-assembling each request. It is
built for scripting sites that have no API -- logging in, walking paginated results, posting a form -- where you want
requests-level control but not a full JavaScript engine like Selenium or Playwright.

turbohtml replaces the parsing and form-reading half of that stack. Once a page is fetched, turbohtml parses it with a
WHATWG-conformant C parser and exposes the form controls through typed methods, so ``select_form``, reading and setting
fields, and collecting the pairs to submit all become calls on the parsed tree. The HTTP session, navigation, and the
actual POST stay out of scope: you drive those with ``requests`` or `httpx <https://www.python-httpx.org>`_ and hand
turbohtml's output to your client.

*****************************
 turbohtml vs MechanicalSoup
*****************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - MechanicalSoup
    - - Scope
      - HTML parsing, CSS queries, and typed form-control reading/writing on the parsed tree
      - Stateful browser: HTTP session, link/form navigation, and submission on top of requests + BeautifulSoup
    - - Feature breadth
      - Spec parser, CSS/XPath selectors, serialization, typed form data; no HTTP or session state
      - ``StatefulBrowser`` with cookies, history, form auto-fill and submit; parsing delegated to BeautifulSoup
    - - Performance
      - WHATWG parser in C; see below
      - Bounded by requests I/O and BeautifulSoup's Python parsing
    - - Typing
      - Ships type stubs; ``field_value``, ``checked``, and ``form_data`` are typed
      - No bundled type stubs
    - - Dependencies
      - Self-contained C extension, no runtime deps
      - Depends on ``requests`` and ``beautifulsoup4`` (plus a BeautifulSoup parser backend)
    - - Maintenance
      - Actively developed alongside the turbohtml parser
      - Actively maintained, thin layer over its two dependencies

Feature overlap
===============

The surface you can port 1:1 is the form-reading and page-querying half:

- Selecting a form on the page: ``browser.select_form("form")`` -> :meth:`~turbohtml.Node.select_one` or
  :meth:`~turbohtml.Node.find`.
- Reading a control's value: ``form[name]`` -> :attr:`~turbohtml.Element.field_value`.
- Setting a control's value: ``browser[name] = value`` / ``form.set(name, value)`` ->
  :attr:`~turbohtml.Element.field_value` assignment.
- Checkbox and radio state: ``form.check(name)`` -> :attr:`~turbohtml.Element.checked`.
- The name/value pairs a submit would send: what ``browser.submit_selected()`` posts ->
  :meth:`~turbohtml.Element.form_data`.

What turbohtml adds
===================

- A WHATWG-conformant HTML parser in C rather than BeautifulSoup's Python tree builder, so malformed markup is fixed up
  the way a browser would.
- :meth:`~turbohtml.Element.form_data` applies the WHATWG form-submission rules directly: unnamed, disabled, and button
  controls are skipped, unchecked checkboxes and radios contribute nothing, and a ``select`` yields one pair per
  selected option. MechanicalSoup reproduces this by walking the BeautifulSoup tree itself.
- Full CSS selector support plus XPath for locating forms and fields, not just tag/attribute lookups.
- Bundled type stubs for the form API.
- No runtime dependencies: turbohtml is a single C extension, where MechanicalSoup pulls in ``requests`` and
  ``beautifulsoup4``.

What MechanicalSoup has that turbohtml does not
===============================================

- **Stateful browsing.** ``StatefulBrowser`` keeps the current page, form, cookies, and history, and ``open``,
  ``follow_link``, and ``submit_selected`` drive them. turbohtml has no HTTP or session layer. Workaround: fetch with
  ``requests`` or ``httpx`` (which manage cookies and redirects for you) and parse each response with turbohtml.
- **One-call form submission.** ``submit_selected`` reads the form, builds the request, and posts it in a single call,
  honoring the form's ``action`` and ``method``. turbohtml gives you the data via :meth:`~turbohtml.Element.form_data`;
  you POST it yourself. No equivalent one-call submit.
- **Link and page navigation helpers.** ``links()``, ``follow_link``, and ``get_current_page`` operate on browser state.
  turbohtml can find the same anchors with :meth:`~turbohtml.Node.select`, but resolving them against the current URL
  and fetching is your code's job.
- **``launch_browser``** to open the current page in a real browser for debugging has no turbohtml equivalent.

Performance
===========

Not directly benchmarked.

****************
 How to migrate
****************

Swap the import and let your HTTP client stay separate from parsing:

.. code-block:: python

    import mechanicalsoup

    browser = mechanicalsoup.StatefulBrowser()

becomes

.. code-block:: python

    import requests
    from turbohtml import parse

API mapping:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `MechanicalSoup <https://mechanicalsoup.readthedocs.io/>`__
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

Before and after, reading a value and collecting the submission pairs:

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

The list :meth:`~turbohtml.Element.form_data` returns drops straight into :func:`urllib.parse.urlencode` or a
``requests``/``httpx`` ``data=`` argument, so submitting is one call on your own client:

.. code-block:: python

    import requests

    response = requests.post(action_url, data=form.form_data(), timeout=30)

**********************
 Gotchas and pitfalls
**********************

- **No session state.** MechanicalSoup carried cookies and the current page across ``open``/``submit`` calls. With
  turbohtml you reuse a ``requests.Session`` (or an ``httpx.Client``) for cookies and redirects, and re-parse each
  response; turbohtml holds no navigation state.
- **Submission is spec-driven, not a tree walk.** :meth:`~turbohtml.Element.form_data` follows the WHATWG rules:
  unnamed, disabled, and button controls are skipped, unchecked checkbox/radio controls contribute nothing, and a
  ``select`` produces one pair per selected option. If you relied on MechanicalSoup including a differently-shaped set
  of controls, compare the ``form_data`` output against what you posted before.
- **Selector reaches the wrong form on multi-form pages.** ``select_one("form")`` returns the first match. On a page
  with several forms, pass a specific selector (an ``id``, ``name``, or ``action`` attribute) to reach the one you mean,
  the same way you would target a form in the browser.
- **Parse bytes, not text.** Hand :func:`turbohtml.parse` the response ``content`` (bytes) so turbohtml applies WHATWG
  encoding detection, rather than pre-decoding to ``str`` with a guessed codec.
