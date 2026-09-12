from __future__ import annotations

import json
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - the conformance oracle runs the installed vnu.jar
from pathlib import Path
from typing import Final, NamedTuple

import pytest

from turbohtml import Element, parse
from turbohtml._html import _conformance_check, _conformance_filter
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
    "tag",
    [
        pytest.param("acronym", id="acronym-uninterned"),
        pytest.param("applet", id="applet"),
        pytest.param("basefont", id="basefont"),
        pytest.param("big", id="big"),
        pytest.param("bgsound", id="bgsound"),
        pytest.param("blink", id="blink-uninterned"),
        pytest.param("center", id="center"),
        pytest.param("dir", id="dir"),
        pytest.param("font", id="font"),
        pytest.param("frame", id="frame"),
        pytest.param("frameset", id="frameset"),
        pytest.param("isindex", id="isindex"),
        pytest.param("listing", id="listing"),
        pytest.param("marquee", id="marquee"),
        pytest.param("nobr", id="nobr"),
        pytest.param("noframes", id="noframes"),
        pytest.param("plaintext", id="plaintext"),
        pytest.param("spacer", id="spacer-uninterned"),
        pytest.param("strike", id="strike"),
        pytest.param("tt", id="tt"),
        pytest.param("xmp", id="xmp"),
    ],
)
def test_conformance_obsolete_elements(tag: str) -> None:
    assert "obsolete-element" in [message.code for message in check(Element(tag)).messages]


def test_conformance_custom_element_is_not_obsolete() -> None:
    assert "obsolete-element" not in [message.code for message in check(Element("custom-element")).messages]


@pytest.mark.parametrize(
    ("tag", "attr", "flagged"),
    [
        pytest.param("p", "align", True, id="align-any-element"),
        pytest.param("p", "background", True, id="background-any-element"),
        pytest.param("p", "bgcolor", True, id="bgcolor-any-element"),
        pytest.param("p", "bordercolor", True, id="bordercolor-any-element"),
        pytest.param("p", "cellpadding", True, id="cellpadding-any-element"),
        pytest.param("p", "cellspacing", True, id="cellspacing-any-element"),
        pytest.param("p", "clear", True, id="clear-any-element"),
        pytest.param("p", "compact", True, id="compact-any-element"),
        pytest.param("p", "hspace", True, id="hspace-any-element"),
        pytest.param("p", "noshade", True, id="noshade-any-element"),
        pytest.param("p", "nowrap", True, id="nowrap-any-element"),
        pytest.param("p", "valign", True, id="valign-any-element"),
        pytest.param("p", "vspace", True, id="vspace-any-element"),
        pytest.param("body", "alink", True, id="alink-on-body"),
        pytest.param("body", "link", True, id="link-on-body"),
        pytest.param("body", "text", True, id="text-on-body"),
        pytest.param("body", "vlink", True, id="vlink-on-body"),
        pytest.param("table", "frame", True, id="frame-on-table"),
        pytest.param("table", "rules", True, id="rules-on-table"),
        pytest.param("script", "language", True, id="language-on-script"),
        pytest.param("a", "charset", True, id="charset-on-a"),
        pytest.param("link", "charset", True, id="charset-on-link"),
        pytest.param("a", "rev", True, id="rev-on-a"),
        pytest.param("link", "rev", True, id="rev-on-link"),
        pytest.param("img", "longdesc", True, id="longdesc-on-img"),
        pytest.param("p", "alink", False, id="alink-not-on-p"),
        pytest.param("p", "link", False, id="link-not-on-p"),
        pytest.param("p", "text", False, id="text-not-on-p"),
        pytest.param("p", "vlink", False, id="vlink-not-on-p"),
        pytest.param("p", "frame", False, id="frame-not-on-p"),
        pytest.param("p", "rules", False, id="rules-not-on-p"),
        pytest.param("p", "language", False, id="language-not-on-p"),
        pytest.param("p", "charset", False, id="charset-not-on-p"),
        pytest.param("p", "rev", False, id="rev-not-on-p"),
        pytest.param("p", "longdesc", False, id="longdesc-not-on-p"),
        pytest.param("p", "title", False, id="conforming-attribute"),
    ],
)
def test_conformance_obsolete_attributes(tag: str, attr: str, *, flagged: bool) -> None:
    element = Element(tag, {attr: "x"})
    assert ("obsolete-attribute" in [message.code for message in check(element).messages]) is flagged


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


def test_check_returns_the_verdict_with_the_findings() -> None:
    valid, findings = _conformance_check(parse("<title>t</title><html lang=en><img>"))
    assert (valid, [finding[0] for finding in findings]) == (False, ["img-missing-alt"])


def test_check_is_valid_with_only_a_warning() -> None:
    valid, findings = _conformance_check(parse("<title>t</title><html lang=en><section><p>x</p></section>"))
    assert (valid, [finding[1] for finding in findings]) == (True, ["warning"])


_ERROR = ConformanceMessage("img-missing-alt", "error", "m", 1, 0)
_WARNING = ConformanceMessage("missing-lang", "warning", "m", 1, 0)


def test_the_filter_keeps_only_the_named_severity_in_order() -> None:
    assert _conformance_filter((_WARNING, _ERROR, _WARNING), "warning") == (_WARNING, _WARNING)


class _Unlabelled(NamedTuple):
    """A record whose severity is not a string, so it can never equal one."""

    severity: int


def test_the_filter_skips_a_message_whose_severity_is_not_a_str() -> None:
    assert _conformance_filter((_Unlabelled(1), _ERROR), "error") == (_ERROR,)  # ty: ignore[invalid-argument-type]


def test_the_filter_needs_a_severity_on_every_message() -> None:
    with pytest.raises(AttributeError):
        _conformance_filter((object(),), "error")  # ty: ignore[invalid-argument-type]  # the attribute read is the point


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(([_ERROR], "error"), id="messages-is-a-list"),
        pytest.param(((_ERROR,), 1), id="severity-is-not-a-str"),
    ],
)
def test_the_filter_rejects_bad_arguments(args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        _conformance_filter(*args)  # ty: ignore[invalid-argument-type]  # the argument check is the point


def valid_doc(inner: str) -> str:
    return f"<!DOCTYPE html><html lang=en><head><title>Doc</title></head><body>{inner}</body></html>"


_VNU_CASES: Final = [
    pytest.param(valid_doc("<h1>Heading</h1><p>text</p>"), id="conforming"),
    pytest.param(valid_doc('<img src="x" alt="a cat">'), id="img-with-alt-conforming"),
    pytest.param(valid_doc("<img src=x>"), id="img-missing-alt"),
    pytest.param(valid_doc("<font>x</font>"), id="obsolete-element"),
    pytest.param(valid_doc('<p align="center">x</p>'), id="obsolete-attribute"),
    pytest.param(valid_doc('<span id="a"></span><span id="a"></span>'), id="duplicate-id"),
    pytest.param(valid_doc('<div role="bogus">x</div>'), id="invalid-role"),
    pytest.param("<!DOCTYPE html><html lang=en><head></head><body><p>x</p></body></html>", id="missing-title"),
]


@pytest.mark.parametrize("markup", _VNU_CASES)
@pytest.mark.oracle
def test_conformance_verdict_matches_vnu(markup: str) -> None:
    jar: Final = Path(pytest.importorskip("vnujar").__file__).parent / "vnu.jar"
    if (java := shutil.which("java")) is None or not jar.is_file():
        pytest.skip("a JRE and vnu.jar are required")
    completed: Final = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed Java arguments and in-repository markup
        [java, "-jar", str(jar), "--format", "json", "--stdin", "-"],
        input=markup.encode(),
        capture_output=True,
        check=False,
    )
    assert check_html(markup).valid is not any(
        message["type"] == "error" for message in json.loads(completed.stderr)["messages"]
    )


@pytest.mark.parametrize(
    "depth", [pytest.param(1, id="single"), pytest.param(64, id="nested-64"), pytest.param(256, id="nested-256")]
)
@pytest.mark.parametrize("heading", [pytest.param(False, id="heading-free"), pytest.param(True, id="heading")])
def test_conformance_nested_section_headings(depth: int, *, heading: bool) -> None:
    content: Final = "<h2>Title</h2>" if heading else "<p>Text</p>"
    assert codes(body("<section>" * depth + content + "</section>" * depth)) == (
        [] if heading else ["section-no-heading"] * depth
    )


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        pytest.param(
            '<section id="x"><article id="x"></article></section><section><h2>Title</h2></section>',
            ["section-no-heading", "section-no-heading", "duplicate-id"],
            id="sibling-boundary-and-preorder",
        ),
        pytest.param(
            "<div><section><article></article></section></div><section><h2></h2></section>",
            ["section-no-heading", "section-no-heading", "empty-heading"],
            id="ancestor-sibling-boundary",
        ),
        pytest.param(
            '<section aria-label=""><article title="Title"><section></section></article></section>',
            ["section-no-heading"],
            id="labels-remain-local",
        ),
    ],
)
def test_conformance_section_scan_boundaries(inner: str, expected: list[str]) -> None:
    assert codes(body(inner)) == expected


def test_conformance_section_subtree_excludes_outer_heading() -> None:
    root: Final = parse("<section><article></article></section><h2>Outside</h2>").find("section")
    assert isinstance(root, Element)
    assert [message.code for message in check(root).messages] == ["section-no-heading", "section-no-heading"]
