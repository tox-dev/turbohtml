from __future__ import annotations

import pytest
from fuzz.generation_targets import encoding_observation


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<a><b><e><r><k><p><a>a<p>\n", "a\n", id="adoption-agency-inner-loop-past-three"),
        pytest.param("<table><<!doctype>\n<l>b", "<b\n", id="doctype-flushes-table-text"),
        pytest.param("<select><table><td>b<tr><", "<b", id="select-closed-by-table-start"),
        pytest.param("<table>\n<p><li > ", " \n", id="list-item-after-foster-paragraph"),
        pytest.param("<table><button><button > <!>x", " x", id="table-start-tag-reprocessed"),
        pytest.param("<pre></e>\n", "\n", id="pre-newline-after-other-token"),
    ],
)
def test_encoding_oracle_accepts_whatwg_tree_construction(markup: str, expected: str) -> None:
    assert encoding_observation(markup.encode()) == expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("</a>x", "x", id="end-tag-without-formatting-element"),
        pytest.param("<a><table></a>", "", id="formatting-element-out-of-scope"),
        pytest.param("<a><b></a>", "", id="formatting-element-not-current"),
        pytest.param("<a><p>x</a>y", "xy", id="furthest-block-is-the-formatting-element"),
        pytest.param("<a><b><p></a>", "", id="clone-between-formatting-and-block"),
        pytest.param("<a><b><p><i></a>", "", id="clone-with-inner-children"),
        pytest.param("<table><a><p>x</a>", "x", id="foster-parented-formatting-element"),
        pytest.param("<table><tr><td><a><p>x</a>", "x", id="foster-parent-in-cell"),
        pytest.param("<a><table><p></a>", "", id="formatting-element-under-table"),
        pytest.param("<table><a>x</a>", "x", id="formatting-element-in-table"),
        pytest.param("<p><a><div></a>", "", id="formatting-element-past-div"),
        pytest.param("<a><b><i><p></a>x", "x", id="two-clones-between-formatting-and-block"),
        pytest.param(f"<a><div>{'<ul><li>' * 4}x</a>y", "xy", id="outer-loop-runs-to-its-limit"),
        pytest.param("<table><p>x</p></table>", "x", id="paragraph-in-table"),
    ],
)
def test_encoding_oracle_adoption_agency_shapes(markup: str, expected: str) -> None:
    assert encoding_observation(markup.encode()) == expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<li>a<li>b", "ab", id="list-item-closes-list-item"),
        pytest.param("<dd>a<dt>b", "ab", id="definition-term-closes-description"),
        pytest.param("<dt>a<dd>b", "ab", id="definition-description-closes-term"),
        pytest.param("<p>a<li>b", "ab", id="list-item-closes-paragraph"),
        pytest.param("<table><li>x", "x", id="list-item-stops-at-table"),
        pytest.param("<div><li>x", "x", id="list-item-passes-div"),
    ],
)
def test_encoding_oracle_list_item_shapes(markup: str, expected: str) -> None:
    assert encoding_observation(markup.encode()) == expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<table><p><li>x", "x", id="foster-paragraph-then-list-item"),
        pytest.param("<table><button><button>x", "x", id="nested-button-in-table"),
        pytest.param("<select><table>", "", id="table-start-tag-in-select"),
        pytest.param("<table>x<!doctype>y", "xy", id="text-before-doctype-in-table"),
        pytest.param("<pre>\nx", "x", id="pre-leading-newline-dropped"),
    ],
)
def test_encoding_oracle_table_and_select_shapes(markup: str, expected: str) -> None:
    assert encoding_observation(markup.encode()) == expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<frameset>x ", " ", id="frameset-whitespace-after-letter"),
        pytest.param("<frameset>w\t", "\t", id="frameset-tab-after-letter"),
        pytest.param("<frameset>x", "", id="frameset-letter-ignored"),
        pytest.param("</br><frameset>/", "/", id="end-tag-br-closes-frameset-window"),
        pytest.param("</br>\t<frameset>", "\t", id="end-tag-br-then-whitespace"),
        pytest.param("<frameset></frameset>y ", " ", id="after-frameset-whitespace-after-letter"),
        pytest.param("<frameset></frameset>x", "", id="after-frameset-letter-ignored"),
    ],
)
def test_encoding_oracle_frameset_and_end_tag_br(markup: str, expected: str) -> None:
    assert encoding_observation(markup.encode()) == expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<select>a<select>b", "ab", id="select-start-closes-open-select"),
        pytest.param("<select>a</select>b", "ab", id="select-end-closes-select"),
        pytest.param("<p></select>x", "x", id="select-end-without-select"),
        pytest.param("<select><b>x</select>y", "xy", id="select-end-pops-formatting"),
        pytest.param("<math><mi><select>x</select>y", "xy", id="select-reset-passes-foreign"),
        pytest.param("<table><select>x</select>y", "xy", id="select-reset-to-table"),
        pytest.param("<select><pre>\nx", "x", id="select-pre-newline-dropped"),
        pytest.param("<table><pre>\nx", "x", id="table-pre-newline-dropped"),
        pytest.param("<select><xmp><b>x", "<b>x", id="select-keeps-xmp-raw-text"),
        pytest.param("<select><textarea>x", "x", id="select-keeps-textarea"),
    ],
)
def test_encoding_oracle_select_in_body_shapes(markup: str, expected: str) -> None:
    assert encoding_observation(markup.encode()) == expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<table><td><textarea>\n", "", id="cell-textarea-newline-dropped"),
        pytest.param("<table><caption><listing>\n", "", id="caption-listing-newline-dropped"),
        pytest.param("<table><td>\nx", "\nx", id="cell-whitespace-kept"),
    ],
)
def test_encoding_oracle_cell_and_caption_newline_shapes(markup: str, expected: str) -> None:
    assert encoding_observation(markup.encode()) == expected
