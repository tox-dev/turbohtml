"""Behavioral coverage of the HTML5 authoring-conformance checker, through the public API."""

from __future__ import annotations

import pytest

from turbohtml import parse
from turbohtml.conformance import ConformanceMessage, ConformanceReport, check, check_html

CLEAN = '<html lang="en"><head><title>Doc</title></head><body><h1>Hi</h1></body></html>'


def codes(markup: str) -> list[str]:
    return [message.code for message in check_html(markup).messages]


def body(inner: str) -> str:
    return f'<html lang="en"><head><title>Doc</title></head><body>{inner}</body></html>'


def test_conformance_clean_document_is_valid() -> None:
    report = check_html(CLEAN)
    assert report.valid is True
    assert report.messages == ()


def test_conformance_report_is_truthy_when_valid() -> None:
    assert bool(check_html(CLEAN)) is True


def test_conformance_report_is_falsy_with_an_error() -> None:
    assert bool(check_html(body("<img src=x>"))) is False


def test_conformance_error_makes_document_invalid() -> None:
    assert check_html(body("<img src=x>")).valid is False


def test_conformance_warning_leaves_document_valid() -> None:
    report = check_html(body("<section><p>text</p></section>"))
    assert report.valid is True
    assert [message.code for message in report.warnings] == ["section-no-heading"]


def test_conformance_info_leaves_document_valid() -> None:
    report = check_html(body('<style type="text/css">a{color:red}</style>'))
    assert report.valid is True
    assert [message.code for message in report.infos] == ["redundant-type-attribute"]


def test_conformance_severity_views_partition_the_messages() -> None:
    report = check_html(
        '<html><body><img src=x><section><p>t</p></section><style type="text/css">a{}</style></body></html>'
    )
    assert {message.code for message in report.errors} == {"img-missing-alt", "missing-title"}
    assert {message.code for message in report.warnings} == {"section-no-heading", "missing-lang"}
    assert {message.code for message in report.infos} == {"redundant-type-attribute"}
    assert set(report.errors + report.warnings + report.infos) == set(report.messages)


def test_conformance_message_carries_code_severity_and_position() -> None:
    (message,) = check_html(body("<img src=x>")).messages
    assert isinstance(message, ConformanceMessage)
    assert message.code == "img-missing-alt"
    assert message.severity == "error"
    assert message.message == "img element has no alt attribute"
    assert message.line == 1
    assert message.column > 0


def test_conformance_check_accepts_a_parsed_document() -> None:
    assert check(parse(CLEAN)).valid is True


def test_conformance_check_on_a_subtree_skips_document_rules() -> None:
    div = parse(body('<div><img src=x><span id="a"></span><span id="a"></span></div>')).find("div")
    assert div is not None
    assert sorted(message.code for message in check(div).messages) == ["duplicate-id", "img-missing-alt"]


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        pytest.param("<img src=x>", "img-missing-alt", id="img-no-alt"),
        pytest.param('<img src=x alt="a photo">', None, id="img-with-alt"),
        pytest.param('<area href="x">', "area-missing-alt", id="area-href-no-alt"),
        pytest.param('<area href="x" alt="a">', None, id="area-href-with-alt"),
        pytest.param("<area>", None, id="area-without-href"),
        pytest.param('<input type="image">', "input-image-missing-alt", id="input-image-no-alt"),
        pytest.param('<input type="image" alt="a">', None, id="input-image-with-alt"),
        pytest.param('<input type="text">', None, id="input-not-image"),
        pytest.param("<input>", None, id="input-no-type"),
        pytest.param("<input type>", None, id="input-type-valueless"),
    ],
)
def test_conformance_alt_text_rules(inner: str, expected: str | None) -> None:
    found = [message.code for message in check_html(body(inner)).messages]
    assert (expected in found) is (expected is not None)
    if expected is None:
        assert found == []


@pytest.mark.parametrize(
    "inner",
    [
        pytest.param("<center>x</center>", id="center"),
        pytest.param('<font size="3">x</font>', id="font"),
        pytest.param("<acronym>x</acronym>", id="acronym-uninterned"),
        pytest.param("<blink>x</blink>", id="blink-uninterned"),
        pytest.param("<marquee>x</marquee>", id="marquee"),
    ],
)
def test_conformance_obsolete_elements(inner: str) -> None:
    assert "obsolete-element" in codes(body(inner))


@pytest.mark.parametrize(
    ("inner", "flagged"),
    [
        pytest.param('<p align="center">x</p>', True, id="align-any-element"),
        pytest.param('<table bgcolor="red"></table>', True, id="bgcolor-any-element"),
        pytest.param('<body link="blue">x</body>', True, id="link-scoped-to-body"),
        pytest.param('<table frame="box"></table>', True, id="frame-scoped-to-table"),
        pytest.param('<p frame="box">x</p>', False, id="frame-not-on-p"),
        pytest.param('<p title="ok">x</p>', False, id="conforming-attribute"),
    ],
)
def test_conformance_obsolete_attributes(inner: str, *, flagged: bool) -> None:
    assert ("obsolete-attribute" in codes(body(inner))) is flagged


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        pytest.param('<div role="bogus">x</div>', "aria-invalid-role", id="invalid-role"),
        pytest.param('<div role="regionx">x</div>', "aria-invalid-role", id="invalid-role-extends-a-valid-name"),
        pytest.param('<div role="widget">x</div>', "aria-invalid-role", id="abstract-role-invalid"),
        pytest.param('<div role="note">x</div>', None, id="valid-role-no-implicit"),
        pytest.param("<nav role=navigation>x</nav>", "aria-redundant-role", id="redundant-landmark"),
        pytest.param('<a href="x" role="link">y</a>', "aria-redundant-role", id="redundant-link-on-anchor"),
        pytest.param('<a role="link">y</a>', None, id="anchor-without-href-not-redundant"),
        pytest.param('<div role="button">x</div>', None, id="role-not-matching-element"),
        pytest.param('<nav role="note">x</nav>', None, id="valid-role-differs-from-implicit"),
        pytest.param('<div role="note alert">x</div>', None, id="role-token-list-uses-first"),
        pytest.param('<div role="  ">x</div>', None, id="whitespace-only-role"),
        pytest.param("<div role>x</div>", None, id="valueless-role"),
    ],
)
def test_conformance_aria_role_rules(inner: str, expected: str | None) -> None:
    found = codes(body(inner))
    if expected is None:
        assert "aria-invalid-role" not in found
        assert "aria-redundant-role" not in found
    else:
        assert expected in found


@pytest.mark.parametrize(
    ("inner", "flagged"),
    [
        pytest.param('<script type="text/javascript">x()</script>', True, id="script-js-mime"),
        pytest.param('<script type="module">x()</script>', False, id="script-module-kept"),
        pytest.param('<script type="application/json">{}</script>', False, id="script-data-block-kept"),
        pytest.param('<style type="text/css">a{}</style>', True, id="style-css-mime"),
        pytest.param('<style type="text/plain">a{}</style>', False, id="style-other-mime"),
        pytest.param("<script>x()</script>", False, id="script-no-type"),
    ],
)
def test_conformance_redundant_type(inner: str, *, flagged: bool) -> None:
    assert ("redundant-type-attribute" in codes(body(inner))) is flagged


@pytest.mark.parametrize(
    ("inner", "flagged"),
    [
        pytest.param("<h1></h1>", True, id="empty-heading"),
        pytest.param("<h3>   </h3>", True, id="whitespace-only-heading"),
        pytest.param("<h2>Title</h2>", False, id="heading-with-text"),
        pytest.param("<h2><img src=x alt=a></h2>", False, id="heading-with-image"),
        pytest.param("<h1><svg><circle/></svg></h1>", True, id="heading-with-only-foreign-content"),
    ],
)
def test_conformance_empty_heading(inner: str, *, flagged: bool) -> None:
    assert ("empty-heading" in codes(body(inner))) is flagged


@pytest.mark.parametrize(
    ("inner", "flagged"),
    [
        pytest.param("<section><p>text</p></section>", True, id="section-no-heading"),
        pytest.param("<article><p>text</p></article>", True, id="article-no-heading"),
        pytest.param("<section><h2>t</h2><p>x</p></section>", False, id="section-with-heading"),
        pytest.param('<section aria-label="Intro"><p>x</p></section>', False, id="section-with-aria-label"),
        pytest.param('<section aria-labelledby="h"><p>x</p></section>', False, id="section-with-aria-labelledby"),
        pytest.param('<section title="Intro"><p>x</p></section>', False, id="section-with-title"),
        pytest.param("<nav><a href=x>y</a></nav>", False, id="nav-not-required-to-have-heading"),
    ],
)
def test_conformance_section_heading(inner: str, *, flagged: bool) -> None:
    assert ("section-no-heading" in codes(body(inner))) is flagged


@pytest.mark.parametrize(
    ("inner", "flagged"),
    [
        pytest.param('<span id="a"></span><span id="a"></span>', True, id="duplicate"),
        pytest.param('<span id="a"></span><span id="b"></span>', False, id="distinct"),
        pytest.param("<span id></span><span id></span>", False, id="valueless-id-ignored"),
        pytest.param('<span id=""></span><span id=""></span>', False, id="empty-id-ignored"),
    ],
)
def test_conformance_duplicate_id(inner: str, *, flagged: bool) -> None:
    assert ("duplicate-id" in codes(body(inner))) is flagged


def test_conformance_missing_title() -> None:
    assert "missing-title" in codes('<html lang="en"><body><p>x</p></body></html>')


def test_conformance_blank_title_counts_as_missing() -> None:
    assert "missing-title" in codes('<html lang="en"><head><title>   </title></head><body>x</body></html>')


@pytest.mark.parametrize(
    ("markup", "flagged"),
    [
        pytest.param("<html><head><title>t</title></head><body>x</body></html>", True, id="no-lang"),
        pytest.param('<html lang="en"><head><title>t</title></head><body>x</body></html>', False, id="lang"),
        pytest.param('<html xml:lang="en"><head><title>t</title></head><body>x</body></html>', False, id="xml-lang"),
    ],
)
def test_conformance_missing_lang(markup: str, *, flagged: bool) -> None:
    assert ("missing-lang" in codes(markup)) is flagged


def test_conformance_document_without_html_reports_only_missing_title() -> None:
    document = parse("<title>x</title>")
    html = document.find("html")
    assert html is not None
    html.extract()
    assert sorted(message.code for message in check(document).messages) == ["missing-title"]


def test_conformance_ignores_foreign_namespace_elements() -> None:
    report = check_html(body('<svg><image href="x"/></svg>'))
    assert [message.code for message in report.messages] == []


def test_conformance_rejects_a_non_node() -> None:
    with pytest.raises(TypeError):
        check("<p>not a node</p>")  # ty: ignore[invalid-argument-type]  # exercises the C type guard


def test_conformance_report_type_is_named_tuple() -> None:
    report = check_html(CLEAN)
    assert isinstance(report, ConformanceReport)
    assert report == (True, ())
