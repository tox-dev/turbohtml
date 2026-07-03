"""
turbohtml.extract: pull content and data out of HTML.

Home for the extraction result types and (as the campaign lands) the content/date/url helpers that replace
``trafilatura``/``readability-lxml``/``newspaper3k``/``goose3``/``justext``/``htmldate``/``courlan``/``extruct``.
The extraction itself runs through the node methods (``node.article()``, ``node.links()``, ``node.tables()``,
``node.structured_data()``); the records they return are re-exported here for discoverability and also stay
importable from the package root.

:func:`boilerplate` adds the per-paragraph view those methods do not give: it segments a page into paragraph units
and marks each one good or boilerplate, the call shape ``justext`` and ``boilerpy3`` expose. The classification is a
thin layer over the same C main-content scoring :meth:`turbohtml.Node.main_content` runs, refined per paragraph by
the length and link-density thresholds a frozen :class:`Extraction` config carries.

The URL helpers live here directly: :func:`clean_url`, :func:`normalize_url`, and :func:`extract_links`, configured
by a frozen :class:`UrlCleaning`, replace ``courlan`` and the ``w3lib.url`` canonicalization surface.

:func:`microdata` gives the ``microdata`` library's ``get_items(html)`` entry point over the same C walk
:meth:`turbohtml.Document.microdata` runs, returning the :class:`MicrodataItem` records whose ``.get``/``.get_all``/
``.json`` accessors mirror that library's ``Item``. :func:`opengraph` gives the ``opengraph`` library's
``OpenGraph(html=...)`` shape over :meth:`turbohtml.Document.opengraph`: an :class:`OpenGraph` mapping of the
``og:``-stripped keys with an :meth:`~OpenGraph.is_valid` check.

:func:`dates` recovers a page's publication or modification date from its ``<meta>`` tags, JSON-LD, ``<time>``
elements, and URL, the standalone entry point ``htmldate`` exposes; a frozen :class:`DateExtraction` carries its
knobs and it returns a :class:`PublicationDate` naming the signal the date came from.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, NamedTuple

from ._article import Article
from ._dates import DateExtraction, PublicationDate, dates
from ._html import Element, parse
from ._links import Link
from ._structured_data import MicrodataItem, StructuredData
from ._urls import UrlCleaning, clean_url, extract_links, normalize_url

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "Article",
    "DateExtraction",
    "Extraction",
    "Link",
    "MicrodataItem",
    "OpenGraph",
    "Paragraph",
    "PublicationDate",
    "StructuredData",
    "UrlCleaning",
    "boilerplate",
    "clean_url",
    "dates",
    "extract_links",
    "microdata",
    "normalize_url",
    "opengraph",
]

_HEADINGS: Final = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_UNITS: Final[list[str]] = [*_HEADINGS, "p", "li", "pre", "blockquote", "td", "dd", "dt", "figcaption"]
"""The block elements a page segments into; a unit holding another unit is a container, not a paragraph."""


class Paragraph(NamedTuple):
    """
    One paragraph unit of a page and its classification, the record :func:`boilerplate` returns.

    ``text`` is the unit's whitespace-normalized visible text (never empty; a blank unit is not reported).
    ``is_boilerplate`` is ``True`` for navigation, footers, sidebars, and any in-content unit that fails the
    :class:`Extraction` thresholds; the ``False`` entries concatenate to the article. ``is_heading`` marks
    ``<h1>``-``<h6>`` units whatever their classification.
    """

    text: str
    is_boilerplate: bool
    is_heading: bool


@dataclass(frozen=True, slots=True)
class Extraction:
    """
    Options for :func:`boilerplate`: the per-paragraph thresholds refining the main-content scoring.

    :param min_length: a non-heading paragraph inside the content body shorter than this many normalized characters
        is still boilerplate. The default 25 is the floor the C scoring itself uses for a paragraph to contribute.
    :param max_link_density: a paragraph whose text sits inside links beyond this fraction is boilerplate wherever
        it lives; 0.5 tolerates prose with citations while dropping link lists that survive inside the content body.
    :param keep_headings: with ``True`` a heading inside the content body is content regardless of ``min_length``
        (headings are short by nature); with ``False`` headings face the same length threshold as prose, justext's
        ``no_headings`` mode.
    """

    min_length: int = 25
    max_link_density: float = 0.5
    keep_headings: bool = True

    def __post_init__(self) -> None:
        """Reject a negative length floor and a link-density bound outside the unit interval."""
        if self.min_length < 0:
            msg = f"min_length must be non-negative, got {self.min_length}"
            raise ValueError(msg)
        if not 0.0 <= self.max_link_density <= 1.0:
            msg = f"max_link_density must be within [0.0, 1.0], got {self.max_link_density}"
            raise ValueError(msg)

    @classmethod
    def justext(cls) -> Extraction:
        """Justext's defaults: its 70-character ``length_low`` floor and 0.2 ``max_link_density``."""
        return cls(min_length=70, max_link_density=0.2)


_DEFAULT: Final = Extraction()


def microdata(html: str, /) -> list[MicrodataItem]:
    """
    Extract a page's top-level HTML Microdata items, the successor to ``microdata.get_items(html)``.

    Each returned :class:`~turbohtml.MicrodataItem` carries the ``itemtype`` as
    :attr:`~turbohtml.MicrodataItem.type`, the ``itemid`` as :attr:`~turbohtml.MicrodataItem.id`, and the ``itemprop``
    values under :attr:`~turbohtml.MicrodataItem.properties`, with :meth:`~turbohtml.MicrodataItem.get`,
    :meth:`~turbohtml.MicrodataItem.get_all`, and :meth:`~turbohtml.MicrodataItem.json` matching the library's ``Item``.
    Shorthand for :meth:`turbohtml.Document.microdata` when a page string is all you hold.

    :param html: the page markup.
    :returns: one :class:`~turbohtml.MicrodataItem` per top-level ``itemscope`` element, in document order.
    """
    return parse(html).microdata()


_OG_PREFIX: Final = "og:"
_OG_REQUIRED: Final = ("title", "type", "image", "url")
"""The Open Graph protocol's four required properties; the check behind :meth:`OpenGraph.is_valid`."""


class OpenGraph(Mapping[str, str]):
    """
    A page's Open Graph metadata as a read-only mapping, the record :func:`opengraph` returns.

    Keys are the ``og:`` property names with that prefix stripped (``og:title`` reads as ``og["title"]``), matching the
    ``opengraph`` library's ``OpenGraph`` dict. The mapping supports the full read surface -- ``og["title"]``,
    ``"title" in og``, ``og.get("title")``, iteration, and equality against a plain ``dict`` -- and adds
    :meth:`is_valid`.
    """

    __slots__ = ("_properties",)

    def __init__(self, properties: dict[str, str], /) -> None:
        """Wrap the already prefix-stripped ``og:`` property mapping :func:`opengraph` builds."""
        self._properties = properties

    def __getitem__(self, key: str) -> str:
        """Return the value of the ``og:<key>`` property, raising :exc:`KeyError` when the page lacks it."""
        return self._properties[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate the prefix-stripped property names in document order."""
        return iter(self._properties)

    def __len__(self) -> int:
        """Return the number of ``og:`` properties the page carries."""
        return len(self._properties)

    def __repr__(self) -> str:
        """Render as ``OpenGraph({...})`` around the underlying property mapping."""
        return f"OpenGraph({self._properties!r})"

    def is_valid(self) -> bool:
        """
        Return whether the page carries the four properties the Open Graph protocol requires, each non-empty.

        Mirrors the ``opengraph`` library's ``is_valid``: every one of ``og:title``, ``og:type``, ``og:image``, and
        ``og:url`` is present with a non-empty value. The ``opengraph_py3`` fork also demands ``og:description``; the
        `Open Graph protocol <https://ogp.me/>`_ does not, so it is not required here.
        """
        return all(self._properties.get(name) for name in _OG_REQUIRED)


def opengraph(html: str, /) -> OpenGraph:
    """
    Extract a page's Open Graph metadata, the successor to ``opengraph.OpenGraph(html=...)``.

    Reads the ``og:`` ``<meta>`` tags :meth:`turbohtml.Document.opengraph` gathers, drops the ``twitter:`` tags that
    method also returns, and strips the ``og:`` prefix from each key so ``og["title"]`` reads the ``og:title`` tag.

    :param html: the page markup.
    :returns: an :class:`OpenGraph` mapping of the prefix-stripped ``og:`` properties, empty when the page has none.
    """
    tags = parse(html).opengraph()
    return OpenGraph({key[len(_OG_PREFIX) :]: value for key, value in tags.items() if key.startswith(_OG_PREFIX)})


def boilerplate(html: str, options: Extraction | None = None, /) -> list[Paragraph]:
    """
    Segment a page into paragraph units and classify each as content or boilerplate, in document order.

    The successor to ``justext.justext`` and ``boilerpy3``'s per-block ``is_content``: the C main-content scoring
    picks the content body exactly as :meth:`turbohtml.Node.main_content` does, every unit outside it is
    boilerplate, and a unit inside it must still clear the ``options`` length and link-density thresholds. A page
    with no scoring content body (a stub, pure navigation) classifies every unit as boilerplate.

    :param html: the page markup.
    :param options: the classification thresholds; defaults to :class:`Extraction`.
    :returns: one :class:`Paragraph` per non-blank unit.
    """
    thresholds = options or _DEFAULT
    document = parse(html)
    content = document.main_content()
    paragraphs = []
    for unit in document.find_all(_UNITS):
        if unit.find(_UNITS) is not None:  # a container whose nested units are the real paragraphs
            continue
        if text := " ".join(unit.text.split()):
            paragraphs.append(Paragraph(text, _is_boilerplate(unit, text, content, thresholds), unit.tag in _HEADINGS))
    return paragraphs


def _is_boilerplate(unit: Element, text: str, content: Element | None, thresholds: Extraction) -> bool:
    """Classify one non-blank unit: outside the content body, link-dense, or below the length floor is boilerplate."""
    if content is None or content not in unit.ancestors:
        return True
    linked = sum(len(anchor.text) for anchor in unit.find_all("a"))
    if linked and linked / len(unit.text) > thresholds.max_link_density:
        return True
    if unit.tag in _HEADINGS and thresholds.keep_headings:
        return False
    return len(text) < thresholds.min_length
