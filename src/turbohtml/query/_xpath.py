"""
Smart-string xpath results, mirroring lxml's ``_ElementUnicodeResult``.

When :meth:`turbohtml.Node.xpath` is called with ``smart_strings=True``, an
attribute or ``text()`` value comes back as an :class:`XPathString`: a ``str``
that also remembers the element it was selected from, so ``result.getparent()``
walks back into the tree and ``result.is_attribute`` / ``result.attrname``
identify where it came from. The C core constructs these; importing this module
registers the type with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from turbohtml._html import Element, _register_xpath_string  # Element stays importable so autodoc resolves it

if TYPE_CHECKING:
    from typing_extensions import Self


# isinstance(result, str) must hold for callers, and the C core builds the value
# through str.__new__, so this stays a real str subclass (FURB189 suppressed).
class XPathString(str):  # ruff:ignore[subclass-builtin]
    """
    A string xpath result that remembers the element it came from.

    :param value: the string value itself, the attribute or ``text()`` result.
    :param parent: the element the value was selected from, returned by :meth:`getparent`.
    :param is_attribute: whether the value came from an attribute rather than element text.
    :param attrname: the attribute name the value came from, or ``None`` for a text value.
    """

    __slots__ = ("_parent", "attrname", "is_attribute", "is_tail", "is_text")

    _parent: Element
    is_attribute: bool
    """Whether the value was selected from an attribute."""
    is_text: bool
    """Whether the value was selected from element text."""
    is_tail: bool
    """Whether the value is tail text; always False, kept for lxml compatibility."""
    attrname: str | None
    """The attribute name the value came from, or None for a text value."""

    def __new__(cls, value: str, parent: Element, is_attribute: bool, attrname: str | None) -> Self:  # ruff:ignore[boolean-type-hint-positional-argument]
        self = str.__new__(cls, value)
        self._parent = parent
        self.is_attribute = is_attribute
        self.is_text = not is_attribute
        self.is_tail = False
        self.attrname = attrname
        return self

    def getparent(self) -> Element:
        """Return the element this attribute or text value was selected from."""
        return self._parent


_register_xpath_string(XPathString)
