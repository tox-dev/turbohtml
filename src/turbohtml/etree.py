"""ElementTree views for consumers that need element text and tail semantics."""

from __future__ import annotations

from typing import Final

from ._html import ElementView, _elementtree_element, _elementtree_subelement
from ._html import _elementtree_context as document_context
from ._html import _elementtree_document_fromstring as document_fromstring
from ._html import _elementtree_fragment_fromstring as fragment_fromstring
from ._html import _elementtree_from_lxml as from_lxml
from ._html import _elementtree_fromstring as fromstring
from ._html import _elementtree_strip_elements as strip_elements
from ._html import _elementtree_strip_tags as strip_tags
from ._html import _elementtree_to_lxml as to_lxml
from ._html import _elementtree_to_lxml_html as to_lxml_html
from ._html import _elementtree_tostring as tostring
from ._html import _ElementXPath as XPath

#: The ElementTree element class, built over the turbohtml tree.
Element: Final = _elementtree_element
#: The ElementTree subelement factory, building children of an :class:`Element`.
SubElement: Final = _elementtree_subelement

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
