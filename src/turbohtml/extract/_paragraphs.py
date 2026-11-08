"""The paragraph segmenter behind :func:`boilerplate`, with its thresholds and paragraph records."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import starmap
from typing import Final, NamedTuple, cast

from turbohtml._html import Element, _boilerplate, parse


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

    __module__ = "turbohtml.extract"

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
        """
        Justext's defaults: its 70-character ``length_low`` floor and 0.2 ``max_link_density``.

        :returns: the extraction with justext's default thresholds.
        """
        return cls(min_length=70, max_link_density=0.2)


_DEFAULT: Final[Extraction] = Extraction()


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
    rows = _boilerplate(
        # a parsed document always has an <html> root, though the type admits None
        cast("Element", document.root),
        document.main_content(),
        thresholds.min_length,
        thresholds.max_link_density,
        thresholds.keep_headings,
    )
    return list(starmap(Paragraph, rows))


class Paragraph(NamedTuple):
    """
    One paragraph unit of a page and its classification, the record :func:`boilerplate` returns.

    ``text`` is the unit's whitespace-normalized visible text (never empty; a blank unit is not reported).
    ``is_boilerplate`` is ``True`` for navigation, footers, sidebars, and any in-content unit that fails the
    :class:`Extraction` thresholds; the ``False`` entries concatenate to the article. ``is_heading`` marks
    ``<h1>``-``<h6>`` units whatever their classification.

    :param text: the unit's whitespace-normalized visible text.
    :param is_boilerplate: whether the unit is navigation, a footer, a sidebar, or other non-article text.
    :param is_heading: whether the unit is an ``<h1>`` to ``<h6>`` heading.
    """

    __module__ = "turbohtml.extract"

    text: str
    is_boilerplate: bool
    is_heading: bool


__all__ = [
    "Extraction",
    "Paragraph",
    "boilerplate",
]
