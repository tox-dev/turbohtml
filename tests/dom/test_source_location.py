"""Granular source locations: Element.source_location under parse(source_locations=True).

The record mirrors parse5's sourceCodeLocationInfo: a start-tag span, an end-tag span (None when the source never
closed the element), and a per-attribute span covering each name="value". Spans are 1-based line, 0-based column, and
code-point offset into the newline-normalized source; the half-open [start_offset, end_offset) slices the construct out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Element, IncrementalParser, SourceSpan, Text, parse, parse_fragment

if TYPE_CHECKING:
    from turbohtml import Node
    from turbohtml._locations import SourceLocation


def child(root: Node, tag: str) -> Element:
    """Find a descendant by tag, narrowing the Element | None result for the checker."""
    found = root.find(tag)
    assert found is not None
    return found


def location(html: str, tag: str) -> SourceLocation:
    """Parse with source locations and return the first matching element's record."""
    element = parse(html, source_locations=True).find(tag)
    assert element is not None
    loc = element.source_location
    assert loc is not None
    return loc


def offsets(span: SourceSpan) -> tuple[int, int]:
    """The half-open code-point range a span covers."""
    return span.start_offset, span.end_offset


def test_start_tag_span_covers_open_angle_through_close_angle() -> None:
    # 1-based line, 0-based column, code-point offset, at both endpoints
    assert location("<div>x</div>", "div").start_tag == SourceSpan(1, 0, 0, 1, 5, 5)


def test_end_tag_span_covers_the_close_tag() -> None:
    loc = location("<div>x</div>", "div")
    assert loc.end_tag is not None
    assert offsets(loc.end_tag) == (6, 12)


def test_attribute_span_slices_the_source() -> None:
    html = '<div id=x class="a b">y</div>'
    loc = location(html, "div")
    assert {name: offsets(span) for name, span in loc.attrs.items()} == {"id": (5, 9), "class": (10, 21)}
    assert html[5:9] == "id=x"
    assert html[10:21] == 'class="a b"'


@pytest.mark.parametrize(
    ("html", "attr", "sliced"),
    [
        pytest.param("<x a>t</x>", "a", "a", id="valueless"),
        pytest.param("<x a=1>t</x>", "a", "a=1", id="unquoted"),
        pytest.param('<x a="1">t</x>', "a", 'a="1"', id="double-quoted"),
        pytest.param("<x a='1'>t</x>", "a", "a='1'", id="single-quoted"),
        pytest.param('<x a="">t</x>', "a", 'a=""', id="empty-quoted"),
        pytest.param("<x a=b&amp;c>t</x>", "a", "a=b&amp;c", id="unquoted-charref"),
    ],
)
def test_attribute_span_shapes(html: str, attr: str, sliced: str) -> None:
    span = location(html, "x").attrs[attr]
    assert html[span.start_offset : span.end_offset] == sliced


def test_two_unquoted_attributes_end_at_their_own_delimiters() -> None:
    html = "<x a=1 b=2>t</x>"
    attrs = location(html, "x").attrs
    assert html[attrs["a"].start_offset : attrs["a"].end_offset] == "a=1"
    assert html[attrs["b"].start_offset : attrs["b"].end_offset] == "b=2"


def test_duplicate_attribute_keeps_the_first_occurrence() -> None:
    html = "<x a=1 a=2>t</x>"
    attrs = location(html, "x").attrs
    assert list(attrs) == ["a"]
    assert html[attrs["a"].start_offset : attrs["a"].end_offset] == "a=1"


def test_element_without_attributes_has_an_empty_attrs_map() -> None:
    assert location("<div>x</div>", "div").attrs == {}


def test_void_element_has_no_end_tag() -> None:
    loc = location("<img src=a.png>", "img")
    assert loc.end_tag is None
    assert offsets(loc.start_tag) == (0, 15)
    assert offsets(loc.attrs["src"]) == (5, 14)


def test_self_closing_void_element_has_no_end_tag() -> None:
    assert location("<br/>", "br").end_tag is None


def test_implicitly_closed_element_has_no_end_tag() -> None:
    # the run of text ends the <p> at EOF, with no </p> in the source
    assert location("<p>hi<div>x</div>", "p").end_tag is None


def test_multiline_spans_carry_line_and_column() -> None:
    loc = location("<p>a\n<b\n c=1>x</b>", "b")
    assert (loc.start_tag.start_line, loc.start_tag.start_col) == (2, 0)
    assert (loc.start_tag.end_line, loc.start_tag.end_col) == (3, 5)
    span = loc.attrs["c"]
    assert (span.start_line, span.start_col) == (3, 1)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("café", id="bmp-2byte"),
        pytest.param("\U0001f600", id="astral-4byte"),
    ],
)
def test_wide_storage_values_do_not_break_offsets(value: str) -> None:
    html = f'<x a="{value}">t</x>'
    span = location(html, "x").attrs["a"]
    assert html[span.start_offset : span.end_offset] == f'a="{value}"'


def test_source_locations_imply_positions() -> None:
    element = child(parse("<div>x</div>", source_locations=True), "div")
    assert element.position == (1, 0)
    assert element.source_location is not None


def test_off_by_default_reports_none() -> None:
    assert child(parse("<div>x</div>"), "div").source_location is None


def test_positions_only_still_reports_none() -> None:
    # line/col tracking on, but the granular record was not requested
    assert child(parse("<div>x</div>", positions=True), "div").source_location is None


def test_source_locations_force_positions_even_with_positions_false() -> None:
    # source_locations subsumes line/col, so it keeps position tracking on despite positions=False
    element = child(parse("<div>x</div>", positions=False, source_locations=True), "div")
    assert element.source_location is not None
    assert element.position == (1, 0)


def test_fragment_source_locations_force_positions() -> None:
    root = parse_fragment("<div>x</div>", "body", positions=False, source_locations=True)
    assert child(root, "div").source_location is not None
    assert child(root, "div").position == (1, 0)


def test_incremental_source_locations_force_positions() -> None:
    parser = IncrementalParser(positions=False, source_locations=True)
    parser.feed("<div>x</div>")
    element = child(parser.close(), "div")
    assert element.source_location is not None
    assert element.position == (1, 0)


def test_synthetic_elements_have_no_location() -> None:
    doc = parse("<p>x</p>", source_locations=True)
    assert child(doc, "html").source_location is None
    assert child(doc, "head").source_location is None
    assert child(doc, "body").source_location is None
    assert child(doc, "p").source_location is not None


def test_explicit_end_tag_on_a_synthetic_element_is_ignored() -> None:
    # <head> is implied by <title>; its explicit </head> closes a synthetic element,
    # which carries no location, so nothing is attached
    doc = parse("<title>t</title></head><p>x</p>", source_locations=True)
    assert child(doc, "head").source_location is None


@pytest.mark.parametrize(
    ("html", "tag", "close"),
    [
        # </body> and </html> switch insertion mode instead of popping their element, and </form> is
        # removed out of stack order; each stamps the end tag through its own path, not the pop path
        pytest.param("<html><body>x</body></html>", "body", "</body>", id="body"),
        pytest.param("<html><body>x</body></html>", "html", "</html>", id="html"),
        pytest.param("<form><input></form>", "form", "</form>", id="form"),
        pytest.param("<form><div>x</form>", "form", "</form>", id="form-closed-with-open-child"),
    ],
)
def test_specially_closed_element_records_its_end_tag(html: str, tag: str, close: str) -> None:
    loc = location(html, tag)
    assert loc.end_tag is not None
    assert html[loc.end_tag.start_offset : loc.end_tag.end_offset] == close


@pytest.mark.parametrize(
    ("html", "tag"),
    [
        # the element the end tag names was never opened in the source, so there is no span to stamp
        pytest.param("<div>x</html>", "html", id="implicit-html"),
        pytest.param("<p>x</body>", "body", id="implicit-body"),
    ],
)
def test_explicit_end_tag_on_a_synthetic_body_or_html_is_ignored(html: str, tag: str) -> None:
    element = parse(html, source_locations=True).find(tag)
    assert element is not None
    assert element.source_location is None


@pytest.mark.parametrize(
    ("html", "tag"),
    [
        pytest.param("<html><body>x", "body", id="body-at-eof"),
        pytest.param("<form><input>", "form", id="form-at-eof"),
    ],
)
def test_specially_closed_element_left_open_has_no_end_tag(html: str, tag: str) -> None:
    assert location(html, tag).end_tag is None


@pytest.mark.parametrize(
    ("html", "node_type"),
    [
        pytest.param("<p>text</p>", Text, id="text-node"),
        pytest.param("<!--c--><p>x</p>", Comment, id="comment-node"),
    ],
)
def test_non_element_nodes_have_no_location(html: str, node_type: type[Node]) -> None:
    node = next(n for n in parse(html, source_locations=True).descendants if isinstance(n, node_type))
    assert node.source_location is None


def test_document_node_has_no_location() -> None:
    assert parse("<p>x</p>", source_locations=True).source_location is None


def test_constructed_element_has_no_location() -> None:
    assert Element("div").source_location is None


def test_fragment_locations_are_relative_to_the_fragment() -> None:
    html = '<div id="a">x</div>'
    root = parse_fragment(html, "body", source_locations=True)
    loc = child(root, "div").source_location
    assert loc is not None
    assert offsets(loc.start_tag) == (0, 12)
    assert html[5:11] == 'id="a"'
    assert offsets(loc.attrs["id"]) == (5, 11)


def test_fragment_off_reports_none() -> None:
    root = parse_fragment("<div>x</div>", "body")
    assert child(root, "div").source_location is None


def test_incremental_parser_tracks_locations_across_feeds() -> None:
    parser = IncrementalParser(source_locations=True)
    parser.feed("<ul>\n  <li>a</li>\n")
    parser.feed('  <li id="x">b</li>\n</ul>')
    items = parser.close().find_all("li")
    second = items[1].source_location
    assert second is not None
    assert second.start_tag.start_line == 3
    assert "id" in second.attrs


def test_incremental_parser_off_reports_none() -> None:
    parser = IncrementalParser()
    parser.feed("<p>x</p>")
    assert child(parser.close(), "p").source_location is None


def test_span_is_a_named_tuple() -> None:
    span = location("<div>x</div>", "div").start_tag
    assert isinstance(span, SourceSpan)
    assert span == (1, 0, 0, 1, 5, 5)
