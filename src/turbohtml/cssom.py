"""
turbohtml.cssom: the CSS Object Model cascade and computed style.

The parse, selector-match, and cascade work lives in the C core (``turbohtml._html``): :func:`computed_style` runs the
CSS Cascade -- collecting every ``<style>`` sheet plus the element chain's inline ``style``, matching the native
selector engine, ordering by origin importance, the style attribute, specificity, and source order, then folding in
inheritance and each property's initial value. The typed, read-only result shapes -- :class:`StyleSheet`,
:class:`RuleList`, :class:`StyleRule`, :class:`StyleDeclaration`, and :class:`ComputedStyle` -- are the turbohtml-native
spelling of the CSSOM ``CSSStyleSheet`` / ``CSSRuleList`` / ``CSSStyleRule`` / ``CSSStyleDeclaration`` interfaces.

The value :func:`computed_style` returns is the *computed* value in the cascade sense: the winning specified value once
``inherit`` / ``initial`` / ``unset`` are resolved. It is not the *used* value. turbohtml renders nothing, so no layout
runs: lengths, percentages, relative units, and system colors come back as written rather than resolved to pixels. This
is the same boundary jsdom and cssstyle draw -- see :doc:`the explanation </explanation/cssom>`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from ._html import _css_computed_style, _css_parse_declarations, _css_parse_rules

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ._html import Element

__all__ = ["ComputedStyle", "RuleList", "StyleDeclaration", "StyleRule", "StyleSheet", "computed_style"]


@final
class StyleDeclaration:
    """
    A read-only block of CSS declarations, the turbohtml spelling of CSSOM's ``CSSStyleDeclaration``.

    Each declaration keeps its property name, value, and ``!important`` flag in source order. A property may appear more
    than once (a later duplicate wins, as the cascade would resolve it); :meth:`get` and indexing return the winning
    value, while iteration and :meth:`properties` visit each distinct property once, in first-seen order.
    """

    __slots__ = ("_items", "_last")

    def __init__(self, items: tuple[tuple[str, str, bool], ...]) -> None:
        """Wrap the ``(name, value, important)`` triples the C parser produced."""
        self._items = items
        self._last = {name: index for index, (name, _, _) in enumerate(items)}

    @classmethod
    def parse(cls, text: str) -> StyleDeclaration:
        """
        Parse a declaration block -- an inline ``style`` attribute or a rule body -- into a declaration.

        :param text: the CSS declaration text, such as ``"color: red; margin: 0 auto"``.
        :returns: the parsed declaration.
        """
        return cls(_css_parse_declarations(text))

    def get(self, name: str, default: str | None = None) -> str | None:
        """
        Return a property's winning value, or ``default`` when it is not set.

        :param name: the property name.
        :param default: the value to return when the property is absent.
        :returns: the property value, or ``default``.
        """
        index = self._last.get(name)
        return default if index is None else self._items[index][1]

    def important(self, name: str) -> bool:
        """
        Return whether a property's winning declaration carries ``!important``.

        :param name: the property name.
        :returns: whether the property is set and marked important.
        """
        index = self._last.get(name)
        return index is not None and self._items[index][2]

    def properties(self) -> tuple[str, ...]:
        """
        Return the distinct property names, in first-seen order.

        :returns: the property names.
        """
        return tuple(self._last)

    @property
    def text(self) -> str:
        """The declaration serialized as ``name: value`` pairs joined by ``"; "``."""
        return "; ".join(
            f"{name}: {value}{' !important' if important else ''}" for name, value, important in self._items
        )

    def __getitem__(self, name: str) -> str:
        """Return a property's winning value, raising :class:`KeyError` when it is not set."""
        index = self._last.get(name)
        if index is None:
            raise KeyError(name)
        return self._items[index][1]

    def __contains__(self, name: str) -> bool:
        """Return whether a property is set."""
        return name in self._last

    def __iter__(self) -> Iterator[str]:
        """Iterate the distinct property names, in first-seen order."""
        return iter(self._last)

    def __len__(self) -> int:
        """Return the number of distinct properties."""
        return len(self._last)

    def __repr__(self) -> str:
        """Return the declaration as ``StyleDeclaration('...')``."""
        return f"StyleDeclaration({self.text!r})"


@final
class StyleRule:
    """One CSS style rule, the turbohtml spelling of CSSOM's ``CSSStyleRule``: a selector and its declarations."""

    __slots__ = ("_style", "selector_text")

    selector_text: str
    """The rule's selector list, verbatim (for example ``"a, .box"``)."""

    def __init__(self, selector_text: str, style: StyleDeclaration) -> None:
        """Bind a selector to its declaration block."""
        self.selector_text = selector_text
        self._style = style

    @property
    def style(self) -> StyleDeclaration:
        """The rule's declaration block."""
        return self._style

    def __repr__(self) -> str:
        """Return the rule as ``StyleRule('selector { ... }')``."""
        return f"StyleRule({self.selector_text!r} {{ {self._style.text} }})"


@final
class RuleList:
    """A read-only sequence of :class:`StyleRule`, the turbohtml spelling of CSSOM's ``CSSRuleList``."""

    __slots__ = ("_rules",)

    def __init__(self, rules: tuple[StyleRule, ...]) -> None:
        """Wrap the rules in document order."""
        self._rules = rules

    def __getitem__(self, index: int) -> StyleRule:
        """Return the rule at an index."""
        return self._rules[index]

    def __iter__(self) -> Iterator[StyleRule]:
        """Iterate the rules in document order."""
        return iter(self._rules)

    def __len__(self) -> int:
        """Return the number of rules."""
        return len(self._rules)

    def __repr__(self) -> str:
        """Return the rule list as ``RuleList([...])``."""
        return f"RuleList({list(self._rules)!r})"


@final
class StyleSheet:
    """
    A parsed stylesheet, the turbohtml spelling of CSSOM's ``CSSStyleSheet``.

    At-rules (``@media``, ``@import``, and the like) are skipped: only top-level style rules join :attr:`rules`.

    :param text: the CSS source, such as the text of a ``<style>`` element.
    """

    __slots__ = ("_rules",)

    def __init__(self, text: str) -> None:
        """Parse CSS source into a stylesheet."""
        self._rules = RuleList(
            tuple(StyleRule(selector, StyleDeclaration(decls)) for selector, decls in _css_parse_rules(text))
        )

    @property
    def rules(self) -> RuleList:
        """The stylesheet's style rules, in document order."""
        return self._rules

    def __repr__(self) -> str:
        """Return the stylesheet as ``StyleSheet(<n> rules)``."""
        return f"StyleSheet({len(self._rules)} rules)"


@final
class ComputedStyle:
    """
    An element's resolved computed style, the read-only result of :func:`computed_style`.

    It exposes one value per supported longhand property (never a shorthand), each already resolved through the cascade,
    inheritance, and initial values. Missing properties fall back to their initial value, so :meth:`get` never returns
    ``None`` for a supported property.
    """

    __slots__ = ("_values",)

    def __init__(self, values: tuple[tuple[str, str], ...]) -> None:
        """Wrap the ``(name, value)`` pairs the cascade resolved."""
        self._values = dict(values)

    def get(self, name: str, default: str | None = None) -> str | None:
        """
        Return a property's computed value, or ``default`` when it is not a supported property.

        :param name: the longhand property name.
        :param default: the value to return for an unsupported property.
        :returns: the computed value, or ``default``.
        """
        return self._values.get(name, default)

    def properties(self) -> tuple[str, ...]:
        """
        Return every supported property name, in the cascade's canonical order.

        :returns: the property names.
        """
        return tuple(self._values)

    def __getitem__(self, name: str) -> str:
        """Return a property's computed value, raising :class:`KeyError` for an unsupported property."""
        return self._values[name]

    def __contains__(self, name: str) -> bool:
        """Return whether a property is a supported longhand."""
        return name in self._values

    def __iter__(self) -> Iterator[str]:
        """Iterate the supported property names."""
        return iter(self._values)

    def __len__(self) -> int:
        """Return the number of supported properties."""
        return len(self._values)

    def __repr__(self) -> str:
        """Return the computed style as ``ComputedStyle(<n> properties)``."""
        return f"ComputedStyle({len(self._values)} properties)"


def computed_style(element: Element) -> ComputedStyle:
    """
    Resolve the CSS cascade for one element and return its computed style.

    The cascade reads every ``<style>`` element in the element's document plus the inline ``style`` attributes along its
    ancestor chain, matches the native selector engine, and orders declarations by importance, the style attribute,
    specificity, and source order before applying inheritance and initial values.

    :param element: the element to compute the style of.
    :returns: the resolved, read-only computed style.
    """
    return ComputedStyle(_css_computed_style(element))
