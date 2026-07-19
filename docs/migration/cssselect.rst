################
 From cssselect
################

.. package-meta:: cssselect scrapy/cssselect

`cssselect <https://cssselect.readthedocs.io/>`_ translates a CSS selector into an equivalent XPath 1.0 expression. It
parses the selector in Python, walks the parsed tree, and emits an XPath string; it does not itself match against a
document. That translator is the engine behind ``lxml.cssselect()``, `parsel <https://parsel.readthedocs.io/>`_ (and so
Scrapy), and `pyquery <https://pyquery.readthedocs.io/>`_, wherever a library wants CSS syntax on top of an XPath
evaluator. Its public surface is small: a ``GenericTranslator`` (XML rules) and an ``HTMLTranslator`` (HTML lowercasing
rules), each exposing ``css_to_xpath(css, prefix="descendant-or-self::")``, plus its ``SelectorError`` /
``SelectorSyntaxError`` / ``ExpressionError`` error types.

:func:`turbohtml.convert.css_to_xpath` does the same job in a single C pass over the parsed selector, and
:class:`turbohtml.convert.GenericTranslator` / :class:`turbohtml.convert.HTMLTranslator` keep cssselect's
translator-object shape and error names so a port is mechanical. Because turbohtml runs a CSS selector engine and an
XPath engine in the same process, the emitted XPath is validated differentially against the native CSS engine rather
than trusted on its own.

************************
 turbohtml vs cssselect
************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - cssselect
    - - Scope
      - CSS-to-XPath translation as one feature of a full HTML5 parser, CSS/XPath query engine, and serializer
      - CSS-to-XPath translation only; matching is left to the host XPath evaluator
    - - Feature breadth
      - Selectors 4 pseudo-classes with full complex arguments in ``:is()``/``:where()``/``:not()``/``:has()``, WHATWG
        form and ``:empty`` semantics
      - Selectors 3 plus documented approximations; several cases carry ``FIXME`` notes in the source
    - - Performance
      - C translator; 5x faster on a bare type selector, 27x-39x on realistic selectors (see below)
      - Pure-Python tokenizer and parser dominate on anything past a trivial selector
    - - Typing
      - Fully typed, ships ``py.typed``
      - No inline type annotations; third-party stubs only
    - - Dependencies
      - None (self-contained C extension)
      - None at runtime
    - - Maintenance
      - Actively developed as part of turbohtml
      - Stable and maintained by the Scrapy project, low change rate

Feature overlap
===============

Portable 1:1 without behavior change:

- ``GenericTranslator().css_to_xpath(css)`` and ``HTMLTranslator().css_to_xpath(css)`` — same method name and signature,
  including the positional ``prefix`` second argument.
- The default ``prefix="descendant-or-self::"`` and its role of scoping each translated arm to the context node's
  subtree.
- A comma-separated selector list translating to an XPath ``|`` union, one arm per selector.
- The two failure modes as typed errors: :class:`turbohtml.SelectorSyntaxError` for a selector the grammar rejects and
  ``ExpressionError`` for a valid selector with no XPath 1.0 form.
- ``Selector.specificity()`` — :func:`turbohtml.convert.css_specificity` returns the same ``(a, b, c)`` triple, one per
  comma-separated selector rather than one per parsed ``Selector`` object.

What turbohtml adds
===================

- A plain :func:`turbohtml.convert.css_to_xpath` function, so callers who never wanted a translator object can skip it.
- Full complex selectors inside logical pseudo-classes: ``:is(nav a)``, ``:where(...)``, ``:not(...)``, and the relative
  ``:has(> a)`` / ``:has(+ a)`` / ``:has(~ a)`` forms.
- WHATWG form-state pseudo-classes — ``:disabled``/``:enabled``/``:checked``/``:required``/``:optional``/
  ``:read-only``/``:read-write`` — including the ``fieldset`` first-``legend`` exemption cssselect marks as a ``FIXME``,
  and ``:nth-child(An+B of S)``.
- Selectors 4 ``:empty`` (whitespace-only elements match) and the WHATWG case-insensitive attribute set (``type``,
  ``lang``, ``rel``, ...) compared case-insensitively, as browsers do.
- Context-free output: every emitted predicate avoids bare ``position()`` tests, so a translated fragment keeps its
  meaning when embedded in a larger expression.
- The native CSS engine itself (:meth:`turbohtml.Node.select`), so translation to XPath is optional rather than the only
  way to run a selector.

What cssselect has that turbohtml does not
==========================================

- A true XML / case-sensitive translation mode. cssselect's ``GenericTranslator`` matches element and attribute names
  case-sensitively; turbohtml always applies the HTML lowercasing rules. ``HTMLTranslator(xhtml=True)`` accepts and
  records the flag for signature compatibility but does not change the output. No equivalent for genuinely
  case-sensitive XML selection.
- The non-standard cssselect extensions ``:contains()`` and ``[attr!=value]``. These are not CSS and do not parse in
  turbohtml; they raise ``SelectorSyntaxError``. Workaround: use the XPath ``contains(., ...)`` predicate directly, or
  ``:not([attr=value])`` for negated attribute matching.
- ``:scope`` anywhere other than the leftmost compound. turbohtml translates ``:scope > div`` but raises
  ``ExpressionError`` for ``:scope`` deeper in a selector, matching cssselect's own leftmost-only behavior; neither is
  more general here.

Performance
===========

The C translator is 5x faster than cssselect on a bare type selector and 27x to 39x faster on realistic selectors, where
cssselect's Python tokenizer dominates. Each ratio is against turbohtml:

.. bench-table::
    :file: bench/cssselect.json

****************
 How to migrate
****************

Swap the import; cssselect's ``HTMLTranslator`` / ``GenericTranslator`` live under :mod:`turbohtml.convert` with the
same method, and a bare :func:`~turbohtml.convert.css_to_xpath` function is available for call sites that never needed a
translator object.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - cssselect
      - turbohtml
    - - ``from cssselect import HTMLTranslator``
      - ``from turbohtml.convert import HTMLTranslator``
    - - ``HTMLTranslator().css_to_xpath(css)``
      - ``HTMLTranslator().css_to_xpath(css)`` or ``css_to_xpath(css)``
    - - ``GenericTranslator().css_to_xpath(css)``
      - ``GenericTranslator().css_to_xpath(css)`` (HTML rules apply either way)
    - - ``css_to_xpath(css, prefix="//")``
      - ``css_to_xpath(css, prefix="//")``
    - - ``from cssselect import SelectorSyntaxError, ExpressionError``
      - ``from turbohtml.convert import SelectorSyntaxError, ExpressionError``

.. code-block:: python

    # cssselect
    from cssselect import HTMLTranslator

    xpath = HTMLTranslator().css_to_xpath("ul > li.item")

    # turbohtml, either shape
    from turbohtml.convert import HTMLTranslator, css_to_xpath

    xpath = HTMLTranslator().css_to_xpath("ul > li.item")
    xpath = css_to_xpath("ul > li.item")

.. testcode::

    from turbohtml.convert import css_to_xpath

    print(css_to_xpath("div#main a[href^='https']"))

.. testoutput::

    descendant-or-self::div[@id = 'main']/descendant::a[starts-with(@href, 'https')]

The ``prefix`` argument works as in cssselect (default ``descendant-or-self::``). A selector the grammar rejects raises
:class:`turbohtml.SelectorSyntaxError` -- the one error every turbohtml selector-parse path shares, a
:class:`ValueError` -- and a valid selector with no XPath 1.0 form raises ``ExpressionError``, under the
cssselect-shaped ``SelectorError``.

.. testcode::

    from turbohtml.convert import ExpressionError, SelectorSyntaxError, css_to_xpath

    try:
        css_to_xpath("li:")
    except SelectorSyntaxError as error:
        print(error)
    try:
        css_to_xpath(":dir(rtl)")
    except ExpressionError as error:
        print(error)

.. testoutput::

    invalid CSS selector "li:": expected an identifier at position 3
    :dir() cannot be expressed in XPath 1.0

**********************
 Gotchas and pitfalls
**********************

- The emitted string differs from cssselect's; only the selected node-set is the contract. Compare results rather than
  expression text.
- Both translator classes apply the HTML rules (element and attribute names lowercase). turbohtml has no XML
  (case-sensitive) mode, so ``HTMLTranslator(xhtml=True)`` only records the flag.
- ``:scope`` translates only as the leftmost compound (``:scope > div``), matching how cssselect's own translation
  behaves; anywhere else raises ``ExpressionError``.
- cssselect's non-standard extensions ``:contains()`` and ``[attr!=value]`` are not CSS and do not parse; they raise
  ``SelectorSyntaxError``. Use the XPath ``contains(., ...)`` predicate or ``:not([attr=value])`` directly.
- ``[lang|="en"]``, ``[type=CHECKBOX]``, ``li:empty`` on whitespace-only elements, and ``:disabled`` on hidden inputs or
  around ``legend`` select per the current specs, so their node-sets can differ from cssselect's approximations (the
  differential suite pins each divergence).
- ``SelectorSyntaxError`` is :class:`turbohtml.SelectorSyntaxError`, a :class:`ValueError` (not cssselect's
  ``SyntaxError``), unifying it with the CSS matching engine; ``ExpressionError`` subclasses ``RuntimeError`` under
  ``SelectorError``. Code catching cssselect's classes by import must switch the import to :mod:`turbohtml.convert`.
