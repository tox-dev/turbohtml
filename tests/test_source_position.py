"""Source positions on parsed elements: Node.source_line / source_col / position.

Positions are tracked by default and dropped with parse(positions=False). The
convention is 1-based line, 0-based column -- the same as html.parser's getpos
and BeautifulSoup's sourceline/sourcepos -- and unavailable positions read None.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Element, IncrementalParser, Text, parse, parse_fragment

if TYPE_CHECKING:
    from turbohtml import Node


def child(root: Node, tag: str) -> Element:
    """Find a descendant by tag, narrowing the Element | None result for the checker."""
    found = root.find(tag)
    assert found is not None
    return found


@pytest.mark.parametrize(
    ("html", "tag", "expected"),
    [
        pytest.param("<p>x</p>", "p", (1, 0), id="single-element-at-start"),
        pytest.param("<div><span>x</span></div>", "span", (1, 5), id="column-is-zero-based"),
        pytest.param("<a></a><b></b>", "b", (1, 7), id="second-element-same-line"),
        pytest.param("<html>\n<body>\n  <p>x</p></body></html>", "p", (3, 2), id="indented-on-third-line"),
        pytest.param("<ul>\n  <li>a</li>\n  <li>b</li>\n</ul>", "ul", (1, 0), id="list-container"),
        pytest.param("<p class='lead' id='x'>y</p>", "p", (1, 0), id="position-is-the-open-angle"),
        pytest.param("\n\n\n<h1>t</h1>", "h1", (4, 0), id="leading-blank-lines"),
    ],
)
def test_element_position(html: str, tag: str, expected: tuple[int, int]) -> None:
    element = parse(html).find(tag)
    assert element is not None
    assert element.position == expected
    assert (element.source_line, element.source_col) == expected


def test_nested_elements_each_carry_their_own_position() -> None:
    doc = parse("<section>\n  <article>\n    <p>deep</p>\n  </article>\n</section>")
    assert child(doc, "section").position == (1, 0)
    assert child(doc, "article").position == (2, 2)
    assert child(doc, "p").position == (3, 4)


@pytest.mark.parametrize(
    "newline",
    [
        pytest.param("\n", id="lf"),
        pytest.param("\r\n", id="crlf"),
        pytest.param("\r", id="cr"),
    ],
)
def test_newline_normalization_keeps_line_numbers(newline: str) -> None:
    # CRLF and lone CR collapse to one line break, so the line number is invariant
    html = newline.join(["<p>1</p>", "<p>2</p>", "<p>3</p>"])
    assert [p.source_line for p in parse(html).find_all("p")] == [1, 2, 3]


def test_columns_are_unaffected_by_carriage_returns() -> None:
    # a CR only appears at a line end, so a mid-line column matches the LF form
    assert child(parse("a\r\n  <b>x</b>"), "b").source_col == 2


def test_positions_false_drops_every_position() -> None:
    doc = parse("<html><body><p>x</p></body></html>", positions=False)
    for tag in ("html", "body", "p"):
        element = child(doc, tag)
        assert element.source_line is None
        assert element.source_col is None
        assert element.position is None


def test_implied_elements_have_no_source() -> None:
    # html/head/body are fabricated for a bare fragment, so they carry no position
    doc = parse("<p>real</p>")
    assert child(doc, "html").source_line is None
    assert child(doc, "head").position is None
    assert child(doc, "body").source_line is None
    assert child(doc, "p").source_line == 1


@pytest.mark.parametrize(
    ("html", "node_type"),
    [
        pytest.param("<p>text</p>", Text, id="text-node"),
        pytest.param("<!--c--><p>x</p>", Comment, id="comment-node"),
    ],
)
def test_non_element_nodes_have_no_position(html: str, node_type: type[Node]) -> None:
    node = next(n for n in parse(html).descendants if isinstance(n, node_type))
    assert node.source_line is None
    assert node.source_col is None
    assert node.position is None


def test_constructed_element_has_no_position() -> None:
    element = Element("div")
    assert element.source_line is None
    assert element.source_col is None
    assert element.position is None


def test_document_node_has_no_position() -> None:
    doc = parse("<p>x</p>")
    assert doc.source_line is None
    assert doc.position is None


def test_fragment_positions_are_relative_to_the_fragment() -> None:
    root = parse_fragment("<div>\n<span>x</span></div>", "body")
    assert child(root, "div").position == (1, 0)
    assert child(root, "span").position == (2, 0)


def test_fragment_positions_false_drops_them() -> None:
    root = parse_fragment("<div>x</div>", "body", positions=False)
    assert child(root, "div").source_line is None


def test_adoption_agency_clone_keeps_the_original_line() -> None:
    # </i> closes across the block <p>, so the adoption agency clones <i> into <p>;
    # the clone stands in for the same source tag, so both <i> nodes report line 1
    doc = parse("<i><p>x</i>y</p>")
    italics = doc.find_all("i")
    assert len(italics) == 2
    assert [i.source_line for i in italics] == [1, 1]


def test_adoption_agency_without_positions_is_unaffected() -> None:
    doc = parse("<i><p>x</i>y</p>", positions=False)
    italics = doc.find_all("i")
    assert len(italics) == 2
    assert all(i.source_line is None for i in italics)


def test_incremental_parser_tracks_positions() -> None:
    parser = IncrementalParser()
    parser.feed("<ul>\n  <li>a</li>\n")
    parser.feed("  <li>b</li>\n</ul>")
    doc = parser.close()
    assert [item.source_line for item in doc.find_all("li")] == [2, 3]


def test_incremental_parser_positions_false() -> None:
    parser = IncrementalParser(positions=False)
    parser.feed("<p>x</p>")
    assert child(parser.close(), "p").source_line is None


def test_position_survives_a_structural_move() -> None:
    doc = parse("<div>\n  <p>x</p>\n</div>")
    paragraph = child(doc, "p")
    assert paragraph.position == (2, 2)
    paragraph.extract()
    child(doc, "div").append(paragraph)
    assert paragraph.position == (2, 2)
