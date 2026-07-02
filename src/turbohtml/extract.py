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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, NamedTuple

from ._article import Article
from ._html import Element, parse
from ._links import Link
from ._structured_data import MicrodataItem, StructuredData

__all__ = [
    "Article",
    "Extraction",
    "Link",
    "MicrodataItem",
    "Paragraph",
    "StructuredData",
    "boilerplate",
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
