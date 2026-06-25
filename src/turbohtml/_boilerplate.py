"""
Paragraph-level boilerplate classification, the one gap the content scoring does not already cover.

:meth:`turbohtml.Node.main_content` and :meth:`~turbohtml.Node.article` answer *which subtree is the article*; they
hand back a single element. ``justext`` and ``boilerpy3`` answer a finer question -- *for every block of text on the
page, is it content or boilerplate* -- and expose a per-paragraph ``is_boilerplate`` flag. :func:`boilerplate` fills
that gap as a thin layer over the existing C content scoring: it reuses :meth:`~turbohtml.Node.main_content` to locate
the article region, then labels every leaf text block by whether it falls inside that region and clears the
length / link-density thresholds, rather than running a second scoring engine of its own.

The thresholds live on an immutable :class:`Extraction` config with presets approximating ``justext`` and ``goose3``
defaults, mirroring the :class:`~turbohtml.Markdown` / :class:`~turbohtml.clean.Policy` configuration objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from ._html import Element, parse

if TYPE_CHECKING:
    from ._html import Node

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# The leaf text blocks justext/boilerpy3 segment a page into: prose, list items, cells, captions, and headings.
_BLOCK_TAGS = _HEADING_TAGS | {"p", "li", "dd", "dt", "blockquote", "pre", "figcaption", "td", "caption"}
_BLOCK_SELECTOR = ", ".join(sorted(_BLOCK_TAGS))


class Paragraph(NamedTuple):
    """
    One block of text classified as article content or boilerplate.

    Mirrors a ``justext`` / ``boilerpy3`` paragraph: ``text`` is the whitespace-folded block, ``is_boilerplate`` is
    ``True`` when it reads as navigation, chrome, or filler outside the article region, and ``is_heading`` marks an
    ``<h1>``-``<h6>`` block so a caller can keep section titles while dropping the rest.
    """

    text: str
    is_boilerplate: bool
    is_heading: bool


@dataclass(frozen=True)
class Extraction:
    """
    Thresholds for :func:`boilerplate`'s content-versus-boilerplate decision.

    Build one and reuse it across threads; every field has a default tuned to turbohtml's own content scoring, so
    ``Extraction()`` reproduces the no-argument classification. Use the presets to approximate another library's
    defaults -- ``Extraction.justext()`` or ``Extraction.goose3()``.

    :param min_text_length: shortest block, in folded characters, that can still count as content; shorter blocks
        inside the article region are labelled boilerplate (headings are exempt, see ``headings_are_content``).
    :param max_link_density: the largest fraction of a block's text that may sit inside ``<a>`` links before the block
        is labelled boilerplate, the menu/list-of-links signal; between 0 and 1 inclusive.
    :param headings_are_content: keep an ``<h1>``-``<h6>`` inside the article region as content regardless of its
        length; set ``False`` to drop headings as boilerplate.
    """

    min_text_length: int = 25
    max_link_density: float = 0.5
    headings_are_content: bool = True

    def __post_init__(self) -> None:
        """Reject thresholds outside their valid ranges up front, so the classifier never has to."""
        if self.min_text_length < 0:
            msg = "min_text_length must not be negative"
            raise ValueError(msg)
        if not 0.0 <= self.max_link_density <= 1.0:
            msg = "max_link_density must be between 0 and 1"
            raise ValueError(msg)

    @classmethod
    def justext(cls) -> Extraction:
        """
        Approximate ``justext``'s stock thresholds: a long minimum block and a strict link-density cap.

        :returns: a config tuned to ``justext``'s defaults.
        """
        return cls(min_text_length=70, max_link_density=0.2)

    @classmethod
    def goose3(cls) -> Extraction:
        """
        Approximate ``goose3``'s more permissive extraction: a short minimum block and a loose link-density cap.

        :returns: a config tuned to ``goose3``'s defaults.
        """
        return cls(min_text_length=20, max_link_density=0.65)


def _fold(text: str) -> str:
    """Collapse a block's runs of whitespace to single spaces and trim the ends, like the renderers."""
    return " ".join(text.split())


def _leaf_blocks(root: Node) -> list[Element]:
    """The block elements holding their own text -- those with no nested block descendant -- in document order."""
    blocks = [node for node in root.descendants if isinstance(node, Element) and node.tag in _BLOCK_TAGS]
    nested = set(blocks)
    containers = {ancestor for block in blocks for ancestor in block.ancestors if ancestor in nested}
    return [block for block in blocks if block not in containers]


def _link_density(block: Element, text_length: int) -> float:
    """The fraction of a block's folded text that sits inside ``<a>`` links, capped at 1."""
    link_chars = sum(len(_fold(anchor.text)) for anchor in block.select("a"))
    return min(link_chars / text_length, 1.0)


def _classify(block: Element, text: str, *, in_content: bool, options: Extraction) -> Paragraph:
    """Label one leaf block as content or boilerplate against the article region and the thresholds."""
    is_heading = block.tag in _HEADING_TAGS
    if is_heading:
        is_boilerplate = not (options.headings_are_content and in_content)
    else:
        long_enough = len(text) >= options.min_text_length
        unlinked = _link_density(block, len(text)) <= options.max_link_density
        is_boilerplate = not (in_content and long_enough and unlinked)
    return Paragraph(text, is_boilerplate, is_heading)


def boilerplate(markup: str | bytes, options: Extraction | None = None, /) -> list[Paragraph]:
    """
    Classify every text block of a page as article content or boilerplate.

    The article region comes from the same C content scoring as :meth:`~turbohtml.Node.main_content`; each leaf text
    block is then content when it falls inside that region and clears the :class:`Extraction` thresholds, and
    boilerplate otherwise. This is the per-paragraph ``is_boilerplate`` view ``justext`` and ``boilerpy3`` expose,
    where :meth:`~turbohtml.Node.article` returns only the single winning element.

    :param markup: the HTML to classify, as text or bytes.
    :param options: the thresholds to apply; ``None`` uses :class:`Extraction`'s defaults.
    :returns: one :class:`Paragraph` per non-empty text block, in document order.
    """
    settings = options if options is not None else _DEFAULT
    document = parse(markup)
    content = document.main_content()
    inside = set() if content is None else {content, *content.descendants}
    return [_classify(block, text, in_content=block in inside, options=settings) for block in _leaf_blocks(document) if (text := _fold(block.text))]


_DEFAULT = Extraction()


__all__ = [
    "Extraction",
    "Paragraph",
    "boilerplate",
]
