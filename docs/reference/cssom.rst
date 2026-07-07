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
