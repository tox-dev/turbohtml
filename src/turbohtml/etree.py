"""ElementTree views for consumers that need element text and tail semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol

from ._html import (
    ElementView,
)
from ._html import ElementXPath as XPath
from ._html import (
    elementtree_context as document_context,
)
from ._html import (
    elementtree_document_fromstring as document_fromstring,
)
from ._html import (
    elementtree_element as _elementtree_element,
)
from ._html import (
    elementtree_fragment_fromstring as fragment_fromstring,
)
from ._html import (
    elementtree_from_lxml as from_lxml,
)
from ._html import (
    elementtree_fromstring as fromstring,
)
from ._html import (
    elementtree_strip_elements as strip_elements,
)
from ._html import (
    elementtree_strip_tags as strip_tags,
)
from ._html import (
    elementtree_subelement as _elementtree_subelement,
)
from ._html import elementtree_to_lxml as to_lxml
from ._html import (
    elementtree_to_lxml_html as to_lxml_html,
)
from ._html import elementtree_tostring as tostring

if TYPE_CHECKING:
    from collections.abc import Mapping


class ElementFactory(Protocol):  # simplint: ignore[unexported-public-name]  # preserve star exports
    """Create an element from a tag, attributes and extra attributes."""  # simplint: ignore[function-docstring]  # API

    def __call__(self, tag: str, attrib: Mapping[str, str] | None = None, **extra: str) -> ElementView: ...


class SubElementFactory(Protocol):  # simplint: ignore[unexported-public-name]  # preserve star exports
    """Create an element as the last child of ``parent``."""  # simplint: ignore[function-docstring]  # public protocol

    def __call__(
        self, parent: ElementView, /, tag: str, attrib: Mapping[str, str] | None = None, **extra: str
    ) -> ElementView: ...


#: The ElementTree element class, built over the turbohtml tree.
Element: Final[ElementFactory] = _elementtree_element
#: The ElementTree subelement factory, building children of an :class:`Element`.
SubElement: Final[SubElementFactory] = _elementtree_subelement

__all__ = [
    "Element",
    "ElementView",
    "SubElement",
    "XPath",
    "document_context",
    "document_fromstring",
    "fragment_fromstring",
    "from_lxml",
    "fromstring",
    "strip_elements",
    "strip_tags",
    "to_lxml",
    "to_lxml_html",
    "tostring",
]
