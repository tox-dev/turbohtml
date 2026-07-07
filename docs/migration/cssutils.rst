###############
 From cssutils
###############

.. package-meta:: cssutils jaraco/cssutils

`cssutils <https://cssutils.readthedocs.io/>`_ is a Python CSS Object Model library. It parses a stylesheet or an inline
declaration into a ``CSSStyleSheet`` of ``CSSStyleRule`` objects, each with a ``selectorText`` and a
``CSSStyleDeclaration`` you can read, edit, and re-serialize. It is the tool a BeautifulSoup- or lxml-based scraper
reaches for when it needs to read the rules inside a ``<style>`` block rather than just match selectors.

:mod:`turbohtml.cssom` covers that same CSSOM surface -- :class:`~turbohtml.cssom.StyleSheet`,
:class:`~turbohtml.cssom.RuleList`, :class:`~turbohtml.cssom.StyleRule`, and :class:`~turbohtml.cssom.StyleDeclaration`
are the turbohtml-native spelling of ``CSSStyleSheet`` / ``CSSRuleList`` / ``CSSStyleRule`` / ``CSSStyleDeclaration`` --
and adds the piece cssutils leaves out: :func:`~turbohtml.cssom.computed_style` runs the CSS cascade against a parsed
document to answer ``getComputedStyle``. cssutils reads the rules; turbohtml reads the rules and tells you which one
wins for an element.

***********************
 turbohtml vs cssutils
***********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - cssutils
    - - Scope
      - Read-only CSSOM plus the cascade (``getComputedStyle``) over a parsed document
      - Read/write CSSOM: parse, edit, and re-serialize stylesheets and declarations
    - - Cascade
      - :func:`~turbohtml.cssom.computed_style` resolves origin, specificity, order, importance, inheritance, and
        initial values
      - None -- cssutils models the rules but does not cascade them
    - - Engine
      - Parse, selector match, and cascade in a C extension, sharing the native selector engine
      - Pure-Python parser and object model
    - - Mutation
      - Result shapes are read-only
      - Declarations and rules are mutable and serialize back to CSS
    - - Typing
      - Fully typed, ``py.typed``
      - No bundled type stubs
    - - Dependencies
      - Self-contained C extension, no runtime deps
      - Pure Python, depends on ``cssselect`` and ``more-itertools``

Feature overlap
===============

The CSSOM read surface ports directly:

- Parse a stylesheet: ``cssutils.parseString(css)`` -> :class:`turbohtml.cssom.StyleSheet`.
- Iterate rules: ``sheet.cssRules`` (a ``CSSRuleList``) -> :attr:`~turbohtml.cssom.StyleSheet.rules` (a
  :class:`~turbohtml.cssom.RuleList`).
- A rule's selector: ``rule.selectorText`` -> :attr:`~turbohtml.cssom.StyleRule.selector_text`.
- A rule's declarations: ``rule.style`` (a ``CSSStyleDeclaration``) -> :attr:`~turbohtml.cssom.StyleRule.style` (a
  :class:`~turbohtml.cssom.StyleDeclaration`).
- Read a property: ``decl.getPropertyValue("color")`` or ``decl["color"]`` ->
  :meth:`~turbohtml.cssom.StyleDeclaration.get` or ``decl["color"]``.
- Read its priority: ``decl.getPropertyPriority("color") == "important"`` ->
  :meth:`~turbohtml.cssom.StyleDeclaration.important`.

What turbohtml adds
===================

- **The cascade.** :func:`~turbohtml.cssom.computed_style` collects every ``<style>`` sheet plus the element's inline
  ``style``, matches the native selector engine, orders by importance, the style attribute, specificity, and source
  order, then applies inheritance and initial values -- the ``getComputedStyle`` that cssutils has no equivalent for. In
  the JavaScript world this is what `jsdom <https://github.com/jsdom/jsdom>`_ and `cssstyle
  <https://github.com/jsdom/cssstyle>`_ provide; turbohtml draws the same computed-value boundary (see
  :doc:`/explanation/cssom`).
- **Shorthand expansion in the cascade.** A computed style exposes longhands only: ``margin`` becomes the four
  ``margin-*`` values, and each is resolved independently.
- **One engine for selectors and the cascade.** The rules match with the same C selector engine as
  :meth:`~turbohtml.Node.select`, so ``:is()``, ``:not()``, ``:has()``, attribute operators, and the combinators all
  behave identically.
- **No dependencies and a typed API**, versus cssutils' ``cssselect`` and ``more-itertools`` requirements.

What cssutils has that turbohtml does not
=========================================

- **Mutation and re-serialization.** cssutils declarations and rules are read/write and serialize back to CSS text;
  turbohtml's result shapes are read-only. To rewrite CSS, edit the source text or use cssutils.
- **At-rules.** cssutils models ``@media``, ``@import``, ``@font-face``, and the rest; turbohtml skips at-rules and
  cascades only top-level style rules.
- **Property-value validation and normalization.** cssutils validates values and can normalize them; turbohtml returns
  the specified value as written.

Performance
===========

Not directly benchmarked -- cssutils has no cascade to compare :func:`~turbohtml.cssom.computed_style` against, and the
CSSOM parse is a small part of either library. turbohtml's cascade runs in the C core; its ``computed-style`` benchmark
resolves the computed style of every element on a styled page.

****************
 How to migrate
****************

Reading a stylesheet's rules ports one call at a time:

.. code-block:: python

    import cssutils

    sheet = cssutils.parseString("a { color: blue } .box { padding: 4px 8px }")
    for rule in sheet.cssRules:
        if rule.type == rule.STYLE_RULE:
            print(rule.selectorText, rule.style.getPropertyValue("color"))

becomes

.. testcode::

    from turbohtml.cssom import StyleSheet

    sheet = StyleSheet("a { color: blue } .box { padding: 4px 8px }")
    for rule in sheet.rules:
        print(rule.selector_text, rule.style.get("color"))

.. testoutput::

    a blue
    .box None

To go beyond the rules and ask which value actually applies to an element -- the step cssutils cannot take -- parse the
document and call :func:`~turbohtml.cssom.computed_style`:

.. testcode::

    import turbohtml
    from turbohtml.cssom import computed_style

    doc = turbohtml.parse(
        "<html><head><style>a { color: blue } #x { color: red !important }</style></head>"
        "<body><a id=x style='color: green'>link</a></body></html>"
    )
    print(computed_style(doc.select_one("#x"))["color"])

.. testoutput::

    red
