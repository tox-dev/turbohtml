#####################################
 Resolve an element's computed style
#####################################

Run the CSS cascade over a parsed document and read the resolved value of any property with
:func:`turbohtml.cssom.computed_style`, or inspect a stylesheet's rules with :class:`~turbohtml.cssom.StyleSheet`.

***********************************
 Compute a property for an element
***********************************

Parse the page, select the element, and call :func:`~turbohtml.cssom.computed_style`. The result is a read-only
:class:`~turbohtml.cssom.ComputedStyle` mapping every supported longhand to its resolved value. The cascade reads the
``<style>`` sheets and the inline ``style``, so the ``!important`` rule below beats the element's own inline color:

.. testcode::

    import turbohtml
    from turbohtml.cssom import computed_style

    doc = turbohtml.parse(
        "<html><head><style>p { color: gray } #intro { color: teal !important }</style></head>"
        "<body><p id=intro style='color: red'>Hi</p></body></html>"
    )
    style = computed_style(doc.select_one("#intro"))
    print(style["color"])
    print(style.get("font-size"))

.. testoutput::

    teal
    medium

*************************************
 Read inherited and shorthand values
*************************************

A property that is not set on an element takes its parent's computed value when it inherits, or its initial value
otherwise. Shorthands such as ``margin`` are expanded to their longhands, so ask for ``margin-top`` rather than
``margin``:

.. testcode::

    doc = turbohtml.parse(
        "<html><head><style>div { color: navy; margin: 1px 2px 3px 4px }</style></head>"
        "<body><div><span>x</span></div></body></html>"
    )
    span = computed_style(doc.select_one("span"))
    print(span["color"])
    div = computed_style(doc.select_one("div"))
    print(div["margin-top"], div["margin-right"], div["margin-bottom"], div["margin-left"])

.. testoutput::

    navy
    1px 2px 3px 4px

**********************
 Inspect a stylesheet
**********************

To read the rules of a stylesheet without cascading them, parse it with :class:`~turbohtml.cssom.StyleSheet`. Each
:class:`~turbohtml.cssom.StyleRule` carries its ``selector_text`` and a :class:`~turbohtml.cssom.StyleDeclaration`;
at-rules such as ``@media`` are skipped:

.. testcode::

    from turbohtml.cssom import StyleSheet

    sheet = StyleSheet("a { color: blue } @media print { a { color: black } } .box { padding: 4px 8px }")
    for rule in sheet.rules:
        print(rule.selector_text, "->", rule.style.text)

.. testoutput::

    a -> color: blue
    .box -> padding: 4px 8px

The value :func:`~turbohtml.cssom.computed_style` returns is the computed value, not the used value that a rendering
engine would produce; :doc:`/explanation/cssom` covers that boundary.
