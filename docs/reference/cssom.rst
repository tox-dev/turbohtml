#######
 CSSOM
#######

.. module:: turbohtml.cssom

The CSS Object Model: parse stylesheets into typed rules and resolve the cascade to a computed style.

:func:`computed_style` runs the CSS Cascade for one element -- it collects every ``<style>`` sheet in the element's
document plus the inline ``style`` along its ancestor chain, matches the native selector engine, orders the declarations
by origin importance, the style attribute, specificity, and source order, then applies inheritance and each property's
initial value. The parse, selector match, and cascade all run in the C core; the classes here are the typed, read-only
result shapes. They are the turbohtml-native spelling of the CSSOM ``CSSStyleSheet`` / ``CSSRuleList`` /
``CSSStyleRule`` / ``CSSStyleDeclaration`` interfaces.

The value :func:`computed_style` returns is the *computed* value, not the *used* value: turbohtml renders nothing, so no
layout runs and lengths, percentages, relative units, and system colors come back as written rather than resolved to
pixels. See :doc:`/explanation/cssom` for that boundary and the property set the cascade covers.

A ``:has()`` argument whose ``:scope`` sits where no memo can answer for every element, such as ``i:has(~ s:is(:scope ~
*) ~ x:is(:scope ~ b ~ *))``, walks the elements past each element it styles, so styling a long sibling run costs time
quadratic in its length. The document funds those walks: 64 compound tests for each of its nodes and each character of
its rules' selectors, on top of a 67,108,864-test base. :func:`computed_style` raises :exc:`ValueError` once a page
outgrows that, and for every element it styles after that until the document changes. A ``:has()`` argument without
``:scope``, or with it only at its start, spends from it only on a page whose answers outgrow the memory the cascade
keeps for them.

.. autofunction:: computed_style

.. autoclass:: ComputedStyle
    :members:
    :special-members: __getitem__, __contains__, __iter__, __len__

.. autoclass:: StyleSheet
    :members:

.. autoclass:: RuleList
    :members:
    :special-members: __getitem__, __iter__, __len__

.. autoclass:: StyleRule
    :members:

.. autoclass:: StyleDeclaration
    :members:
    :special-members: __getitem__, __contains__, __iter__, __len__
