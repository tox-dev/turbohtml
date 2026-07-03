"""Table extraction: Element.rows()/records() and Node.tables() over a parsed tree."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Element, parse, parse_fragment

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def rows() -> Callable[[str], list[list[str]]]:
    """Parse a fragment, find its first ``<table>``, and read it into a row grid."""

    def extract(html: str) -> list[list[str]]:
        table = parse_fragment(html).find("table")
        assert isinstance(table, Element)
        return table.rows()

    return extract


@pytest.fixture
def records() -> Callable[[str], list[dict[str, str]]]:
    """Parse a fragment, find its first ``<table>``, and read it into header-keyed records."""

    def extract(html: str) -> list[dict[str, str]]:
        table = parse_fragment(html).find("table")
        assert isinstance(table, Element)
        return table.records()

    return extract


ROWS_CASES = [
    pytest.param(
        "<table><tr><th>H1</th><th>H2</th></tr><tr><td>a</td><td>b</td></tr></table>",
        [["H1", "H2"], ["a", "b"]],
        id="simple-grid",
    ),
    pytest.param("<table><tr><td>  hello  </td></tr></table>", [["hello"]], id="cell-text-stripped"),
    pytest.param("<table><tr><td>a<b>b</b>c</td></tr></table>", [["abc"]], id="nested-markup-flattened"),
    pytest.param("<table><tr><td>a</td><td></td></tr></table>", [["a", ""]], id="empty-cell-is-empty-string"),
    pytest.param("<table><tr><td>   </td><td>b</td></tr></table>", [["", "b"]], id="whitespace-cell-is-empty-string"),
    pytest.param(
        "<table><tr><td colspan=2>A</td><td>B</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>",
        [["A", "A", "B"], ["1", "2", "3"]],
        id="colspan-fills-columns",
    ),
    pytest.param(
        "<table><tr><td rowspan=2>x</td><td>c</td></tr><tr><td>d</td></tr></table>",
        [["x", "c"], ["x", "d"]],
        id="rowspan-fills-rows",
    ),
    pytest.param(
        "<table>"
        "<tr><td>a</td><td>b</td><td>c</td></tr>"
        "<tr><td rowspan=2 colspan=2>m</td><td>d</td></tr>"
        "<tr><td>e</td></tr>"
        "</table>",
        [["a", "b", "c"], ["m", "m", "d"], ["m", "m", "e"]],
        id="rowspan-and-colspan",
    ),
    pytest.param(
        "<table><tr><td>a</td><td>b</td><td>c</td></tr><tr><td>1</td></tr></table>",
        [["a", "b", "c"], ["1", "", ""]],
        id="ragged-rows-padded",
    ),
    pytest.param("<table><tr><td>a</td><td>b</td></tr></table>", [["a", "b"]], id="absent-span-defaults-to-one"),
    pytest.param("<table><tr><td colspan>A</td><td>B</td></tr></table>", [["A", "B"]], id="valueless-colspan-is-one"),
    pytest.param("<table><tr><td colspan=0>A</td><td>B</td></tr></table>", [["A", "B"]], id="zero-colspan-is-one"),
    pytest.param('<table><tr><td colspan="x">A</td><td>B</td></tr></table>', [["A", "B"]], id="non-numeric-colspan"),
    pytest.param(
        '<table><tr><td colspan=" 2">A</td></tr><tr><td>1</td><td>2</td></tr></table>',
        [["A", "A"], ["1", "2"]],
        id="leading-whitespace-span-skipped",
    ),
    pytest.param(
        '<table><tr><td colspan="  ">A</td><td>B</td></tr></table>',
        [["A", "B"]],
        id="all-whitespace-span-is-one",
    ),
    pytest.param(
        '<table><tr><td colspan="-2">A</td><td>B</td></tr></table>',
        [["A", "B"]],
        id="leading-non-digit-span-is-one",
    ),
    pytest.param(
        "<table><tr><td rowspan=0>x</td><td>a</td></tr><tr><td>b</td></tr><tr><td>c</td></tr></table>",
        [["x", "a"], ["x", "b"], ["x", "c"]],
        id="zero-rowspan-spans-to-end",
    ),
    pytest.param(
        "<table><tr><td rowspan=5>x</td><td>a</td></tr><tr><td>b</td></tr></table>",
        [["x", "a"], ["x", "b"]],
        id="rowspan-beyond-last-row-clamped",
    ),
    pytest.param(
        "<table><tbody><tr><td rowspan=0>A</td><td>B</td></tr><tr><td>C</td></tr></tbody>"
        "<tbody><tr><td>D</td><td>E</td></tr></tbody></table>",
        [["A", "B"], ["A", "C"], ["D", "E"]],
        id="zero-rowspan-stops-at-row-group-end",
    ),
    pytest.param(
        "<table><thead><tr><td rowspan=0>A</td><td>B</td></tr></thead>"
        "<tbody><tr><td>C</td><td>D</td></tr></tbody></table>",
        [["A", "B"], ["C", "D"]],
        id="zero-rowspan-from-thead-stops-at-thead-end",
    ),
    pytest.param(
        "<table><tbody><tr><td>C</td><td>D</td></tr></tbody>"
        "<tfoot><tr><td rowspan=0>A</td><td>B</td></tr><tr><td>E</td></tr></tfoot></table>",
        [["C", "D"], ["A", "B"], ["A", "E"]],
        id="zero-rowspan-in-tfoot-stops-at-tfoot-end",
    ),
    pytest.param(
        "<table><tbody><tr><td rowspan=3>A</td><td>B</td></tr><tr><td>C</td></tr></tbody>"
        "<tbody><tr><td>D</td></tr></tbody></table>",
        [["A", "B"], ["A", "C"], ["A", "D"]],
        id="fixed-rowspan-crosses-row-group-boundary",
    ),
    pytest.param(
        "<table><tr><td>A</td><td rowspan=2>B</td></tr><tr><td colspan=3>C</td></tr></table>",
        [["A", "B", ""], ["C", "C", "C"]],
        id="overlapping-spans-later-cell-wins",
    ),
    pytest.param(
        "<table><tr><td>A</td><td rowspan=2>B</td></tr><tr></tr></table>",
        [["A", "B"], ["", "B"]],
        id="rowspan-over-empty-row-leaves-hole",
    ),
    pytest.param(
        "<table><tr><td>A</td><td rowspan=2>B</td></tr><tr><td>C</td></tr></table>",
        [["A", "B"], ["C", "B"]],
        id="cell-fills-rowspan-hole",
    ),
    pytest.param(
        "<table><tr><td>a</td><!--note--><style>td{}</style><td>b</td></tr></table>",
        [["a", "b"]],
        id="non-cell-elements-skipped",
    ),
    pytest.param("<table></table>", [], id="empty-table-has-no-rows"),
    pytest.param("<table><tr></tr><tr></tr></table>", [[], []], id="empty-tr-elements-are-empty-lists"),
]


@pytest.mark.parametrize(("html", "expected"), ROWS_CASES)
def test_rows(rows: Callable[[str], list[list[str]]], html: str, expected: list[list[str]]) -> None:
    assert rows(html) == expected


@pytest.mark.parametrize("tag", [pytest.param("td", id="td"), pytest.param("th", id="th")])
def test_both_cell_tags_are_read(rows: Callable[[str], list[list[str]]], tag: str) -> None:
    assert rows(f"<table><tr><{tag}>v</{tag}></tr></table>") == [["v"]]


def test_many_rows_grow_the_snapshot(rows: Callable[[str], list[list[str]]]) -> None:
    # collect_table_rows starts at 8 row slots; >8 rows exercise its regrow.
    body = "".join(f"<tr><td>{index}</td></tr>" for index in range(12))
    assert rows(f"<table>{body}</table>") == [[str(index)] for index in range(12)]


def test_huge_colspan_is_clamped(rows: Callable[[str], list[list[str]]]) -> None:
    grid = rows('<table><tr><td colspan="5000">A</td></tr></table>')
    assert len(grid) == 1
    assert len(grid[0]) == 1000
    assert grid[0][0] == "A"
    assert grid[0][999] == "A"


def _rehome_rows(destination: Element) -> None:
    # The parser always wraps rows in a tbody, so move real <tr> elements under `destination` to give a table
    # rows with no row-group ancestor: a rowspan=0 cell then has no group to stop at and grows to the last row.
    source = parse_fragment(
        "<table><tbody><tr><td rowspan=0>A</td><td>B</td></tr><tr><td>C</td></tr></tbody></table>",
    ).find("table")
    assert isinstance(source, Element)
    body = source.find("tbody")
    assert isinstance(body, Element)
    for row in list(body.children):
        row.extract()
        destination.append(row)


def _table_itself(table: Element) -> Element:
    return table


def _div_under(table: Element) -> Element:
    wrapper = parse_fragment("<div></div>").find("div")
    assert isinstance(wrapper, Element)
    wrapper.extract()
    table.append(wrapper)
    return wrapper


@pytest.mark.parametrize(
    "receiver",
    [pytest.param(_table_itself, id="rows-in-table"), pytest.param(_div_under, id="rows-under-a-div")],
)
def test_zero_rowspan_without_a_row_group_spans_to_table_end(receiver: Callable[[Element], Element]) -> None:
    table = parse_fragment("<table></table>").find("table")
    assert isinstance(table, Element)
    _rehome_rows(receiver(table))
    assert table.rows() == [["A", "B"], ["A", "C"]]


RECORDS_CASES = [
    pytest.param(
        "<table><tr><th>H1</th><th>H2</th></tr><tr><td>a</td><td>b</td></tr></table>",
        [{"H1": "a", "H2": "b"}],
        id="first-row-is-header",
    ),
    pytest.param(
        "<table>"
        "<thead><tr><th>name</th><th>score</th></tr></thead>"
        "<tbody><tr><td>ann</td><td>9</td></tr><tr><td>bob</td><td>7</td></tr></tbody>"
        "</table>",
        [{"name": "ann", "score": "9"}, {"name": "bob", "score": "7"}],
        id="thead-keys-tbody",
    ),
    pytest.param(
        "<table><tr><th>a</th><th>b</th></tr><tr><td rowspan=2>x</td><td>1</td></tr><tr><td>2</td></tr></table>",
        [{"a": "x", "b": "1"}, {"a": "x", "b": "2"}],
        id="spans-resolved-before-keying",
    ),
    pytest.param(
        "<table><tr><th>k</th><th>k</th></tr><tr><td>1</td><td>2</td></tr></table>",
        [{"k": "2"}],
        id="duplicate-header-keeps-rightmost",
    ),
    pytest.param("<table></table>", [], id="empty-table-yields-no-records"),
    pytest.param("<table><tr><th>H1</th><th>H2</th></tr></table>", [], id="header-only-yields-no-records"),
    pytest.param("<table><tr></tr><tr></tr><tr></tr></table>", [{}, {}], id="rows-without-cells-yield-empty-records"),
]


@pytest.mark.parametrize(("html", "expected"), RECORDS_CASES)
def test_records(records: Callable[[str], list[dict[str, str]]], html: str, expected: list[dict[str, str]]) -> None:
    assert records(html) == expected


DOCUMENT_TABLES_CASES = [
    pytest.param(
        "<table><tr><td>a</td></tr></table><p>x</p><table><tr><td>b</td><td>c</td></tr></table>",
        [[["a"]], [["b", "c"]]],
        id="every-table-on-the-page",
    ),
    pytest.param("<p>no tables here</p>", [], id="no-tables-yields-empty-list"),
    pytest.param(
        "<table><tr><td>outer<table><tr><td>inner</td></tr></table></td></tr></table>",
        [[["outerinner"]], [["inner"]]],
        id="nested-table-is-its-own-entry",
    ),
]


@pytest.mark.parametrize(("html", "expected"), DOCUMENT_TABLES_CASES)
def test_document_tables(html: str, expected: list[list[list[str]]]) -> None:
    assert parse(html).tables() == expected


def test_many_tables_grow_the_snapshot() -> None:
    # turbohtml_node_tables starts at 4 grid slots; >4 tables exercise its regrow.
    markup = "".join(f"<table><tr><td>{index}</td></tr></table>" for index in range(7))
    assert parse(markup).tables() == [[[str(index)]] for index in range(7)]


def test_nested_table_rows_belong_to_the_inner_table() -> None:
    outer = parse_fragment(
        "<table><tr><td>outer<table><tr><td>inner</td></tr></table></td></tr></table>",
    ).find("table")
    assert isinstance(outer, Element)
    assert outer.rows() == [["outerinner"]]


def test_tables_called_on_a_table_returns_only_its_nested_tables() -> None:
    outer = parse_fragment("<table><tr><td><table><tr><td>inner</td></tr></table></td></tr></table>").find("table")
    assert isinstance(outer, Element)
    assert outer.tables() == [[["inner"]]]


@pytest.fixture
def non_table_div() -> Element:
    """A ``<div>`` wrapping a table, the wrong receiver for rows()/records()."""
    div = parse_fragment("<div><table><tr><td>a</td></tr></table></div>", "body").find("div")
    assert isinstance(div, Element)
    return div


def test_rows_on_a_non_table_raises_type_error(non_table_div: Element) -> None:
    with pytest.raises(TypeError, match="rows can only be called on a table element"):
        non_table_div.rows()


def test_records_on_a_non_table_raises_type_error(non_table_div: Element) -> None:
    with pytest.raises(TypeError, match="records can only be called on a table element"):
        non_table_div.records()
