################################
 The cascade and computed style
################################

:func:`turbohtml.cssom.computed_style` answers a narrow question: given a parsed document and one element, which value
would win for each CSS property? That is the *cascade*, and turbohtml runs it the way the `CSS Cascade
<https://www.w3.org/TR/css-cascade/>`_ specification describes -- with one deliberate boundary, drawn where a
non-rendering library has to stop.

***********************
 What the cascade does
***********************

For each element the cascade gathers every declaration that could apply and picks a winner per property:

- **Collection.** Every ``<style>`` element in the document contributes its rules, in document order. Each element's
  inline ``style`` attribute contributes a further block. External stylesheets are not fetched -- turbohtml does no I/O.
- **Matching.** Each rule's selector list runs through the same native selector engine as
  :meth:`~turbohtml.Node.select`, under the tree's critical section. A selector the engine cannot parse (a
  pseudo-element such as ``::before``, say) drops its rule rather than failing the whole cascade.
- **Ordering.** Among the matching declarations for a property, the winner is chosen by the cascade sort: ``!important``
  beats normal, the style attribute beats a selector rule, higher `specificity
  <https://www.w3.org/TR/selectors-4/#specificity>`_ beats lower, and a later declaration beats an earlier one. Only the
  author origin exists here; there is no user or user-agent stylesheet, so each property's initial value stands in for
  the user-agent default.
- **Resolution.** The winning value is then resolved against the element's parent: ``inherit`` (and an unset inherited
  property) takes the parent's computed value, ``initial`` takes the property's initial value, and ``unset`` and
  ``revert`` collapse to one of those. Properties are inherited by walking the ancestor chain from the root down, so a
  parent is always computed before its child.

Shorthands are expanded before the sort. ``margin: 1px 2px`` becomes the four ``margin-*`` longhands, ``border-color``
splits across the four sides, and ``overflow`` fills ``overflow-x`` and ``overflow-y``; a computed style therefore only
ever exposes longhands, never a shorthand.

**********************************
 Computed values, not used values
**********************************

The CSS specifications distinguish four value stages: the *specified* value (what the author wrote), the *computed*
value (the specified value with the cascade and inheritance resolved and relative forms turned absolute where possible
without layout), the *used* value (the computed value after layout, so a ``width: 50%`` becomes a pixel count), and the
*actual* value (the used value snapped to the rendering surface).

turbohtml stops at the computed value, and even there it does not perform the sub-resolutions that need font metrics or
a color engine: a length stays ``1em``, a percentage stays ``50%``, and a color stays ``rgb(0, 0, 0)`` rather than being
normalized. Producing a used value would require a layout engine -- a box tree, font shaping, a viewport -- which a
parse -and-query library does not have and does not want. This is the same line `jsdom
<https://github.com/jsdom/jsdom>`_ and `cssstyle <https://github.com/jsdom/cssstyle>`_ draw: they resolve the cascade
but return the pre-layout value. If you need pixel geometry, you need a browser or a rendering engine such as
WeasyPrint; if you need to know which rule wins and what value it carries, that is exactly what
:func:`~turbohtml.cssom.computed_style` gives you.

The cascade is validated differentially against jsdom's ``getComputedStyle`` -- 39 fixtures over every axis above, all
agreeing once color serialization and the boundaries here are accounted for (``tests/conformance``). The one place the
two diverge on a supported property is the ``overflow`` shorthand, which turbohtml expands to
``overflow-x``/``overflow-y`` per CSS Overflow 3 and jsdom leaves at its initial value.

******************
 The property set
******************

The cascade tracks a curated set of common, well-defined longhands -- the inherited text and font properties (``color``,
``font-size``, ``line-height``, ``text-align`` and the like), the box properties (``margin-*``, ``padding-*``,
``border-*-width/style/color``, ``width``, ``height``), and the common layout and paint properties (``display``,
``position``, the offsets, ``float``, ``opacity``, ``background-color``, ``overflow-x/y``). Each carries its inheritance
flag and its initial value from that property's specification. Properties outside the set are ignored rather than
guessed at, and a declaration whose property is unknown is simply dropped -- so
:meth:`~turbohtml.cssom.ComputedStyle.get` returns a value for every property the set covers and ``None`` for anything
else.
