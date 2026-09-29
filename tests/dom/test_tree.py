from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import (
    Comment,
    Document,
    Element,
    Html,
    Indent,
    MicrodataItem,
    Minify,
    ProcessingInstruction,
    Range,
    RdfaItem,
    StructuredData,
    Text,
    _html,
    parse,
    parse_fragment,
    parse_xml,
)
from turbohtml.clean import Policy, sanitize

if TYPE_CHECKING:
    from collections.abc import Callable


def _doc(html: str) -> str:
    return _html._parse_tree(html).rstrip("\n")


def _frag(html: str, context: str) -> str:
    return _html._parse_fragment(html, context).rstrip("\n")


def test_parse_only_returns_none() -> None:
    assert _html._parse_only("<html><body><p>hi & bye</p>") is None


@pytest.mark.parametrize(
    ("call"),
    [
        # bytes on purpose: the stub types these str-only (correct for real use), so ty flags the deliberate
        # misuse; suppress rather than widen the stub, which would drop the very rejection this test asserts.
        pytest.param(lambda: _html._parse_tree(b"x"), id="parse_tree"),  # ty: ignore[invalid-argument-type]  # non-str on purpose
        pytest.param(lambda: _html._parse_only(b"x"), id="parse_only"),  # ty: ignore[invalid-argument-type]  # non-str on purpose
        pytest.param(lambda: _html._parse_fragment(b"x", "div"), id="parse_fragment"),  # ty: ignore[invalid-argument-type]  # non-str
    ],
)
def test_hooks_reject_non_str(call: Callable[[], object]) -> None:
    with pytest.raises(TypeError):
        call()


# Several cases below pin otherwise-unreached decision branches in the tree builder.
# gcov scores each operand of a short-circuit condition separately, so they pin the
# rarer operand value (a non-newline after <pre>, a hidden-input type with a low-ASCII
# byte, an annotation-xml encoding that is not "text/html", an attribute name long
# enough to fill the 128-byte serialization sort buffer, ...).
_LONG_NAME = "z" * 130


@pytest.mark.parametrize(
    ("html", "needle"),
    [
        pytest.param(
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Frameset//EN"><p><table>',
            "|       <table>",
            id="quirks-4.01-frameset-no-system-id",
        ),
        pytest.param(
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN"><p><table>',
            "|       <table>",
            id="quirks-4.01-transitional-no-system-id",
        ),
        pytest.param("<html a=1><html b><p>x", 'b=""', id="merge-valueless-attr"),
        pytest.param(
            "<select multiple><selectedcontent></selectedcontent><option>x</option></select>",
            "<selectedcontent>",
            id="selectedcontent-multiple-select",
        ),
        pytest.param(
            "<select><selectedcontent></selectedcontent>"
            "<option selected>pick<select><option>n</option></select></option></select>",
            "<selectedcontent>",
            id="selectedcontent-into-nested-select",
        ),
        pytest.param("<textarea>\nkept</textarea>", '"kept"', id="textarea-newline-span"),
        pytest.param("<textarea>\r\nkept</textarea>", '"kept"', id="textarea-newline-materialized"),
        pytest.param("<p>a&amp;b</p>", '"a&b"', id="entity-materialized"),
        pytest.param("<body>\U0001f600\U0001f389ok</body>", "\U0001f600\U0001f389ok", id="ucs4-body-text-span"),
        pytest.param("<body><template>in</template>after", '"after"', id="template-close-in-body"),
        pytest.param("<body><template><template>x</template>y</template>", "content", id="nested-template-close"),
        pytest.param(
            "<a><b><big><code><em><font><i><nobr><s><small><strike><strong><tt><u><b><i><div></a>x",
            '"x"',
            id="deep-formatting-afe-growth",
        ),
        pytest.param(
            "<select><selectedcontent></selectedcontent><option class=x>y</option></select>",
            "<selectedcontent>",
            id="node-has-attr-no-match",
        ),
        pytest.param("<p \U0001f600=1>x", '"x"', id="astral-attribute-name"),
        pytest.param('<!DOCTYPE html PUBLIC "\U0001f600">x', '"x"', id="astral-doctype-public-id"),
        pytest.param('<!DOCTYPE html PUBLIC "HTML"><p><table>', "|       <table>", id="quirks-public-id-html"),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-/W3C/DTD HTML 4.0 Transitional/EN"><p><table>',
            "|       <table>",
            id="quirks-public-id-4.0-transitional",
        ),
        # a same-length near-miss of the 4.0-transitional exact id is not quirky, so the
        # <p> closes and <table> becomes its sibling (exercises the exact-match mismatch)
        pytest.param(
            '<!DOCTYPE html PUBLIC "-/W3C/DTD HTML 4.0 Transitional/EX"><p><table>',
            "|     <p>\n|     <table>",
            id="no-quirks-public-id-4.0-transitional-near-miss",
        ),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3O//DTD W3 HTML Strict 3.0//EN//"><p><table>',
            "|       <table>",
            id="quirks-public-id-w3o-strict",
        ),
        # WHATWG initial mode: a -//W3C//DTD HTML 4.01 Frameset/Transitional public id puts
        # the document in quirks mode when the system id is missing OR the empty string (so
        # <table> nests in the still-open <p>); a non-empty system id downgrades it to
        # limited-quirks (no-quirks here), closing the <p> so <table> becomes its sibling
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Frameset//EN"><p><table>',
            "|     <p>\n|       <table>",
            id="quirks-4.01-frameset-missing-sysid",
        ),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Frameset//EN" ""><p><table>',
            "|     <p>\n|       <table>",
            id="quirks-4.01-frameset-empty-sysid",
        ),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" ""><p><table>',
            "|     <p>\n|       <table>",
            id="quirks-4.01-transitional-empty-sysid",
        ),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Frameset//EN" "x"><p><table>',
            "|     <p>\n|     <table>",
            id="limited-quirks-4.01-frameset-nonempty-sysid",
        ),
        pytest.param("<p>" + "x" * 70000, "x" * 40, id="text-larger-than-arena-block"),
        pytest.param("<html a=1><html ő=2>", 'a="1"', id="merge-wide-attr-name"),
        pytest.param("<input type=HIDDEN>", "<input>", id="uppercase-hidden-input-type"),
        pytest.param("<ruby>a<rb>b<rtc>c", "<rtc>", id="ruby-rb-rtc-in-scope"),
        pytest.param("<pre>\nkept", '"kept"', id="pre-drops-leading-newline"),
        pytest.param("<pre>zkept", '"zkept"', id="pre-keeps-non-newline"),
        pytest.param("<listing>\nq", '"q"', id="listing-drops-leading-newline"),
        # after-head whitespace lands under <html> (between head and body); only the rest starts the body (issue #88)
        pytest.param(
            "<head></head>  text",
            '|   <head>\n|   "  "\n|   <body>\n|     "text"',
            id="after-head-whitespace-under-html",
        ),
        pytest.param("<head></head>   ", '|   <head>\n|   "   "\n|   <body>', id="after-head-only-whitespace"),
        pytest.param("<head></head>x", '|   <head>\n|   <body>\n|     "x"', id="after-head-no-leading-whitespace"),
        pytest.param("a\x00b", '"ab"', id="nul-stripped-from-text"),
        pytest.param("<template>" * 9 + "z", "content", id="template-stack-regrow"),
        pytest.param("<table><thead><tr><td>z</table>", "<td>", id="thead-table-scope-cell"),
        pytest.param("<table><tfoot><tr><td>z</table>", "<td>", id="tfoot-table-scope-cell"),
        pytest.param("<form><div>x</form>y", '"xy"', id="form-end-tag-keeps-open-div"),
        pytest.param("<template><form>x</form></template>", "content", id="form-inside-template"),
        pytest.param(
            "<b><i><u><s><tt><big><small><em><strong><code><font><nobr><a><b><i><u><s>x</a>",
            '"x"',
            id="formatting-list-regrow",
        ),
        pytest.param(
            "<p " + " ".join(f"a{index}=1" for index in range(70)) + ">", 'a0="1"', id="element-over-64-attributes"
        ),
        pytest.param("<![CDATA[x]]>", "[CDATA[x]]", id="cdata-in-html-is-bogus-comment"),
        pytest.param("<svg><![CDATA[x]]></svg>", '"x"', id="cdata-in-foreign-is-text"),
        # the CDATA scan runs in the 2-byte and 4-byte tokenizer cores too: a wide char widens the whole input
        pytest.param("<svg><![CDATA[Ω]]></svg>", '"Ω"', id="cdata-foreign-ucs2"),
        pytest.param("<svg><![CDATA[\U0001f600]]></svg>", '"\U0001f600"', id="cdata-foreign-ucs4"),
        pytest.param("<math><mi mathvariant=bold>x</mi></math>", 'mathvariant="bold"', id="mathml-attribute-adjust"),
        pytest.param("<math definitionurl=x></math>", 'definitionURL="x"', id="mathml-definitionurl-cased"),
        pytest.param("<svg definitionurl=x></svg>", 'definitionurl="x"', id="svg-definitionurl-stays-lower"),
        pytest.param("<svg viewBox='0 0 1 1'>x</svg>", 'viewBox="0 0 1 1"', id="svg-camelcase-attribute"),
        pytest.param("<svg><foreignObject>x</foreignObject></svg>", "<svg foreignObject>", id="svg-camelcase-tag"),
        pytest.param(
            "<math><annotation-xml encoding='text/html'><div>x</div></annotation-xml></math>",
            "<div>",
            id="annotation-xml-html-integration",
        ),
        pytest.param("<svg><font color=red>x", "<font>", id="svg-font-attribute-breakout"),
        pytest.param(
            "<math><annotation-xml encodinğ=1><div>x</div></annotation-xml></math>",
            "<div>",
            id="foreign-wide-attribute-name",
        ),
        pytest.param("<svg><font colőr=1>x", "<svg font>", id="svg-font-wide-attribute-no-breakout"),
        pytest.param("<math><mtext><b>x</b></mtext></math>", "<b>", id="mathml-text-integration"),
        pytest.param("<svg><foreignObject><b>x</b></foreignObject></svg>", "<b>", id="svg-html-integration"),
        pytest.param("<svg><a xlink:href=x>y</a></svg>", 'xlink href="x"', id="foreign-namespaced-attribute"),
        # plain xmlns on a foreign element belongs to the xmlns namespace, rendered "xmlns xmlns" (issue #64)
        pytest.param(
            '<svg xmlns="http://www.w3.org/2000/svg">',
            'xmlns xmlns="http://www.w3.org/2000/svg"',
            id="foreign-plain-xmlns",
        ),
        pytest.param(
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">',
            '|       xmlns xlink="http://www.w3.org/1999/xlink"\n|       xmlns xmlns="http://www.w3.org/2000/svg"',
            id="foreign-plain-and-prefixed-xmlns-sorted",
        ),
        pytest.param(
            "<math><mtext><svg><br></svg></mtext></math>", "<br>", id="breakout-stops-at-mathml-text-integration"
        ),
        pytest.param(
            "<svg><foreignObject><math><br></math></foreignObject></svg>",
            "<br>",
            id="breakout-stops-at-html-integration",
        ),
        pytest.param(
            "<math><mtext><svg></br>q</svg></mtext></math>",
            "<svg svg>\n|         <br>",
            id="end-br-breaks-out-to-mathml-integration",
        ),
        pytest.param(
            "<math><annotation-xml encoding='application/xhtml+xml'><div>x</div></annotation-xml></math>",
            "<div>",
            id="annotation-xml-xhtml-integration",
        ),
        pytest.param(
            "<math><annotation-xml encoding='foo/bar'><div>x</div></annotation-xml></math>",
            "<div>",
            id="annotation-xml-non-html-encoding",
        ),
        # the encoding must be an EXACT case-insensitive match; a near miss is not an integration
        # point, so <b> breaks out to body level instead of nesting under annotation-xml (issue #92)
        pytest.param(
            "<math><annotation-xml encoding='text-html'><b>x</b></annotation-xml></math>",
            'encoding="text-html"\n|     <b>',
            id="annotation-xml-encoding-near-miss-breaks-out",
        ),
        # the application/xhtml+xml row was length-only, so any 21-char value matched; now it does not
        pytest.param(
            "<math><annotation-xml encoding='application/xhtml-xml'><b>x</b></annotation-xml></math>",
            'encoding="application/xhtml-xml"\n|     <b>',
            id="annotation-xml-encoding-21-char-near-miss-breaks-out",
        ),
        # an uppercase exact match is still an integration point, so <b> stays nested
        pytest.param(
            "<math><annotation-xml encoding='TEXT/HTML'><b>x</b></annotation-xml></math>",
            'encoding="TEXT/HTML"\n|         <b>',
            id="annotation-xml-encoding-uppercase-integration",
        ),
        pytest.param("<math><mi><div>x</div></mi></math>", "<div>", id="mathml-mi-not-html-integration"),
        pytest.param("<svg><font face=x>y", "<font>", id="svg-font-face-breakout"),
        pytest.param("<svg><font size=x>y", "<font>", id="svg-font-size-breakout"),
        pytest.param("<svg></p>", "<svg svg>\n|     <p>", id="end-p-breaks-out-of-svg"),
        pytest.param("<math></p>", "<math math>\n|     <p>", id="end-p-breaks-out-of-math"),
        pytest.param("<svg></br>", "<svg svg>\n|     <br>", id="end-br-breaks-out-of-svg"),
        pytest.param("<math></br>", "<math math>\n|     <br>", id="end-br-breaks-out-of-math"),
        pytest.param(
            "<math><mtext><svg></p>q</svg></mtext></math>",
            "<svg svg>\n|         <p>",
            id="end-p-breaks-out-to-mathml-integration",
        ),
        pytest.param(
            "<head><base><basefont><bgsound><link><meta><noframes>n</noframes></head>z", "z", id="head-elements"
        ),
        pytest.param(
            "<head></head><base><basefont><bgsound><link><meta><title>t</title>"
            "<style>s</style><script>x</script><noframes>q</noframes>y",
            "y",
            id="after-head-reprocesses-head-elements",
        ),
        pytest.param("<body>z</body><meta><link><script>x</script>", "z", id="after-body-head-elements"),
        pytest.param("</body></html><base>x", "html", id="after-after-body-element"),
        pytest.param("<input type=Hidden>", "<input>", id="mixed-case-hidden-type"),
        pytest.param("<rb>x", '"x"', id="rb-without-ruby-scope"),
        pytest.param("<rtc>x", '"x"', id="rtc-without-ruby-scope"),
        pytest.param("<table>foo", '"foo"', id="table-fosters-leading-text"),
        pytest.param("<table><tr><td>c</td><th>h</table>", "<th>", id="table-row-cells"),
        pytest.param("<table><caption>c</caption><tr><td>x</table>", "<caption>", id="table-caption"),
        pytest.param("<table><tbody><tr><td>x</tr></tbody></table>", "<tbody>", id="table-section-end"),
        pytest.param("<form>a</form>b", "<form>", id="form-in-scope-end-tag"),
        pytest.param("<table><form><input></table>", "<form>", id="form-inside-table"),
        pytest.param("<div><address>a</div>b", "<address>", id="special-element-end-tag-close"),
        pytest.param("<ruby><rt>x", "<rt>", id="ruby-rt-in-scope"),
        pytest.param("<ruby><rp>x", "<rp>", id="ruby-rp-in-scope"),
        pytest.param("<template><form></form></template>", "content", id="form-in-template-scope"),
        pytest.param("<template><form><form>x</template>", "content", id="second-form-in-template"),
        pytest.param("<template><table><tr><th>x", "<th>", id="cell-in-template-sets-row-mode"),
        pytest.param(
            "<select><selectedcontent readonly></selectedcontent><option>x</option></select>",
            "<selectedcontent>",
            id="selectedcontent-attr-same-length",
        ),
        pytest.param(
            "<select><selectedcontent disabled></selectedcontent><option>x</option></select>",
            "<selectedcontent>",
            id="selectedcontent-disabled-attr",
        ),
        pytest.param("<a href=1><a href=2>x", "<a>", id="formatting-noah-ark-attrs"),
        pytest.param("<pre>\r\nx", "<pre>", id="pre-cr-newline-materialized"),
        pytest.param("<textarea>\rx</textarea>", "<textarea>", id="textarea-cr-materialized"),
        pytest.param("<input typő=hidden>", "<input>", id="hidden-type-wide-attr-name"),
        pytest.param("<input type=hiddeő>", "<input>", id="hidden-type-wide-attr-value"),
        pytest.param("<table><tfoot><tr><td>x</td></tr><tr><td>y</table>", "<tfoot>", id="tfoot-table-scope"),
        pytest.param(
            "<math><annotation-xml encoding><div>x</div></annotation-xml></math>",
            "<div>",
            id="annotation-xml-valueless-encoding",
        ),
        pytest.param(
            "<math><annotation-xml encoding='application/foo'><div>x</div></annotation-xml></math>",
            "<div>",
            id="annotation-xml-other-application-encoding",
        ),
        pytest.param(
            "<template><base><basefont><bgsound><link><meta>x</template>", "content", id="template-head-elements"
        ),
        pytest.param(
            "<template><title>t</title><style>s</style><script>x</script><noframes>n</noframes></template>",
            "content",
            id="template-rawtext-head-elements",
        ),
        pytest.param(
            "<template><caption></caption><colgroup></colgroup><tbody></tbody><tfoot></tfoot><thead></thead></template>",
            "content",
            id="template-table-sections",
        ),
        pytest.param("<template><td></td><th></th></template>", "content", id="template-table-cells"),
        pytest.param("<template><col></template>", "content", id="template-col"),
        pytest.param("<head></foo></head>x", "<head>", id="unknown-end-tag-in-head"),
        pytest.param("<head></br></head>x", "<head>", id="end-br-in-head"),
        pytest.param("<template></template><html lang=en>x", "html", id="html-start-reprocess-with-template"),
        pytest.param("<table><div>x</div></table>", "<div>", id="element-fostered-before-table"),
        pytest.param("<template><thead>x", "content", id="template-thead-open"),
        pytest.param("<template><td>x", "content", id="template-td-open"),
        pytest.param("<template><tr>x", "content", id="template-tr-open"),
        pytest.param("<template><div>x", "content", id="template-other-start-tag"),
        pytest.param("<pre>\rx", "<pre>", id="pre-cr-only-materialized"),
        pytest.param("<textarea>z</textarea>", "<textarea>", id="textarea-no-leading-newline"),
        pytest.param("<template><form></form>x</template>", "content", id="form-end-tag-in-template"),
        pytest.param("<table><thead><tr><td>x<tr>", "<thead>", id="table-section-scope-on-row"),
        pytest.param("<table><tr><td>a</td><th>b</th>", "<th>", id="td-and-th-cells"),
        pytest.param("<b id=a t=p><b id=b t=p>x", "<b>", id="afe-multi-attr-mismatch-first"),
        pytest.param("<b id=x><b ie=y>z", "<b>", id="afe-same-length-different-name"),
        pytest.param("<b id=x><b id=yy>z", "<b>", id="afe-different-value-length"),
        pytest.param("<table>x", '"x"', id="foster-text-target-table"),
        pytest.param("<input type>z", "<input>", id="input-valueless-type"),
        pytest.param("<input type=hidde0>z", "<input>", id="input-hidden-type-low-byte"),
        pytest.param("<applet>" * 20 + "x", "<applet>", id="afe-marker-stack-regrow"),
        pytest.param("</br>x", '"x"', id="end-br-before-head"),
        pytest.param("<template><tfoot>a", "content", id="template-tfoot-section"),
        pytest.param("<template><caption>b", "content", id="template-caption-section"),
        pytest.param("<template><colgroup>c", "content", id="template-colgroup-section"),
        pytest.param("<template><tbody>d", "content", id="template-tbody-section"),
        pytest.param("<template><thead>e", "content", id="template-thead-section"),
        pytest.param("<template><td>f", "content", id="template-td-cell"),
        pytest.param("<template><th>g", "content", id="template-th-cell"),
        pytest.param("<dd><address><dt>x", "<dt>", id="dd-walk-skips-address"),
        pytest.param("<dd><div><dt>y", "<dt>", id="dd-walk-skips-div"),
        pytest.param("<rt>x", "<rt>", id="rt-without-ruby-scope"),
        pytest.param("<rp>y", "<rp>", id="rp-without-ruby-scope"),
        pytest.param("<form><template><form>x", "<form>", id="nested-form-in-template-with-form-pointer"),
        pytest.param("<template><table><form>x", "content", id="form-in-table-under-template"),
        pytest.param("<table><tfoot><tbody>x</table>", "<tbody>", id="table-body-after-tfoot-scope"),
        pytest.param("<table><thead><caption>y</table>", "<caption>", id="table-caption-after-thead-scope"),
        pytest.param(
            "<math><annotation-xml q=1><div>x</div></annotation-xml></math>", "<div>", id="annotation-xml-short-attr"
        ),
        pytest.param(
            "<math><annotation-xml encoding=txxxxxxxx><div>x</div></annotation-xml></math>",
            "annotation-xml",
            id="annotation-xml-nine-char-not-te",
        ),
        pytest.param(
            "<math><annotation-xml encoding=texxxhxxx><div>x</div></annotation-xml></math>",
            "<div>",
            id="annotation-xml-te-prefix-h-at-five",
        ),
        pytest.param(
            "<math><annotation-xml encoding=exxxxxxxx><div>x</div></annotation-xml></math>",
            "annotation-xml",
            id="annotation-xml-nine-char-not-t",
        ),
        pytest.param(
            "<math><annotation-xml encoding=texxxxxxx><div>x</div></annotation-xml></math>",
            "annotation-xml",
            id="annotation-xml-te-prefix-no-h",
        ),
        pytest.param(
            "<svg><foreignObject><svg></p>x", "<svg svg>\n|         <p>", id="end-p-breaks-out-to-html-integration"
        ),
        pytest.param("<svg><desc><svg></br>y", "<svg svg>\n|         <br>", id="end-br-breaks-out-to-html-integration"),
        pytest.param("<p " + _LONG_NAME + "=1 m=2>x", "z" * 40, id="attr-name-fills-sort-buffer"),
        pytest.param("<svg " + _LONG_NAME + "=1 m=2>y</svg>", "z" * 40, id="foreign-attr-name-fills-sort-buffer"),
        # a redundant <html> start tag before/in head keeps the head insertion
        # mode, so following head-only content stays in <head> (issue #46)
        pytest.param("<html><html><style>x", "<head>\n|     <style>", id="redundant-html-in-head-keeps-style"),
        pytest.param("<html><html><meta>", "<head>\n|     <meta>", id="redundant-html-before-head-keeps-meta"),
        pytest.param("<html a><html b>", '|   a=""\n|   b=""', id="redundant-html-merges-attributes"),
        # a duplicate <head> start tag is an ignored parse error, not a mode change
        pytest.param("<head><head><meta>", "<head>\n|     <meta>", id="duplicate-head-keeps-meta"),
        pytest.param("<head><head><title>t", "<head>\n|     <title>", id="duplicate-head-keeps-title"),
    ],
)
def test_document_paths(html: str, needle: str) -> None:
    assert needle in _doc(html)


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        # the LF is dropped only when it is the token immediately following the start tag
        pytest.param("<pre>\nX</pre>", "<pre>X</pre>", id="pre-immediate-lf-dropped"),
        # an interposed comment or element means the LF is not immediately following, so it is kept
        pytest.param("<pre><!--c-->\nX</pre>", "<pre><!--c-->\nX</pre>", id="pre-comment-before-lf-kept"),
        pytest.param("<pre><span></span>\nX</pre>", "<pre><span></span>\nX</pre>", id="pre-element-before-lf-kept"),
        pytest.param("<listing><!--c-->\nX</listing>", "<listing><!--c-->\nX</listing>", id="listing-comment-lf-kept"),
        # RCDATA leaves no room for an intervening token, so a leading textarea LF is still dropped
        pytest.param("<textarea>\nX</textarea>", "<textarea>X</textarea>", id="textarea-immediate-lf-dropped"),
    ],
)
def test_leading_newline_skip_only_immediately_after_start_tag(html: str, expected: str) -> None:
    # WHATWG "in body" ignores a leading U+000A only for the token directly following a
    # pre/listing/textarea start tag; a comment or element between them keeps the newline (issue #402)
    assert parse(html).serialize() == f"<html><head></head><body>{expected}</body></html>"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        # a CR from a character reference is decoded after preprocessing, so it reaches tree
        # construction as a real U+000D that the early modes must treat as ignorable whitespace
        pytest.param("&#13;a", "<body>a</body>", id="decimal-cr-leading-ignored"),
        pytest.param("&#xD;a", "<body>a</body>", id="hex-cr-leading-ignored"),
        # a leading CR must not flip frameset-ok, so the frameset still replaces the empty body
        pytest.param("&#13;<frameset></frameset>", "<frameset></frameset>", id="cr-keeps-frameset-ok"),
    ],
)
def test_cr_character_reference_is_tree_whitespace(html: str, expected: str) -> None:
    # U+000D is whitespace in every insertion mode, so a leading &#13; is ignored and does not
    # set frameset-ok to "not ok"; only a literal CR is folded to LF by preprocessing (issue #439)
    assert parse(html).serialize() == f"<html><head></head>{expected}</html>"


@pytest.mark.parametrize("tag", ["caption", "table", "tbody", "tfoot", "thead", "tr", "td", "th"])
def test_table_family_start_tag_pops_select_in_table(tag: str) -> None:
    # "in select in table": a table-family start tag pops the open select and reprocesses,
    # so it never nests inside the select (the select is left empty as a sibling)
    select = parse(f"<table><tr><td><select><{tag}>").find("select")
    assert select is not None
    assert not select.inner_html


def test_non_table_start_tag_stays_in_select_in_table() -> None:
    # a non-table-family start tag is not affected: an option stays inside the select
    select = parse("<table><tr><td><select><option>x").find("select")
    assert select is not None
    assert select.inner_html == "<option>x</option>"


def test_stray_html_in_colgroup_keeps_it_open() -> None:
    # a stray <html> in "in column group" uses the in-body rules (merge attributes, leave the
    # stack), so the colgroup stays open and the next <col> joins it instead of starting a new one
    out = parse("<table><colgroup><col><html lang=en><col>").html
    assert out == ('<html lang="en"><head></head><body><table><colgroup><col><col></colgroup></table></body></html>')


def test_colgroup_fragment_keeps_whitespace_after_ignored_characters() -> None:
    # with no colgroup to pop, each non-whitespace character is ignored in "in column group" and the
    # whitespace between them is still inserted
    assert parse_fragment("a b\nc", "colgroup").inner_html == " \n"


def test_template_column_group_keeps_whitespace_after_ignored_characters() -> None:
    template = parse("<template><col>a b c</template>").find("template")
    assert template is not None
    assert template.inner_html == "<col>  "


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<table>\x00 ", "<table> </table>", id="nul-then-space"),
        pytest.param("<table> \x00 ", "<table>  </table>", id="space-nul-space"),
        pytest.param("<table> \x00", "<table> </table>", id="space-then-nul"),
        # control: a real non-whitespace char still foster-parents the run out of the table
        pytest.param("<table>x\x00 ", "x <table></table>", id="nonspace-fosters"),
    ],
)
def test_nul_in_table_text_keeps_whitespace_inside(html: str, expected: str) -> None:
    # a U+0000 in "in table text" is dropped, so an otherwise-whitespace run is inserted
    # inside the table rather than foster-parented out of it
    body = parse(html).find("body")
    assert body is not None
    assert body.inner_html == expected


@pytest.mark.parametrize(
    ("html", "inner"),
    [
        pytest.param("<b><math><mi></b>", "<b><math><mi></mi></math></b>", id="mathml-mi"),
        pytest.param("<i><math><mo></i>", "<i><math><mo></mo></math></i>", id="mathml-mo"),
        pytest.param(
            "<b><svg><foreignObject><p></b>",
            "<b><svg><foreignObject><p></p></foreignObject></svg></b>",
            id="svg-foreignobject",
        ),
        pytest.param("<b><svg><desc></b>", "<b><svg><desc></desc></svg></b>", id="svg-desc"),
    ],
)
def test_formatting_end_tag_ignored_across_foreign_scope_boundary(html: str, inner: str) -> None:
    # a MathML/SVG integration point is a scope boundary, so the formatting element is not in
    # scope and the end tag is ignored instead of running adoption and splitting the subtree
    body = parse(html).find("body")
    assert body is not None
    assert body.inner_html == inner


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            "<!doctype html><!doctype></p>",
            "<!DOCTYPE html><html><head></head><body></body></html>",
            id="stray-end-tag-after",
        ),
        pytest.param(
            "<!doctype html><!doctype> x",
            "<!DOCTYPE html><html><head></head><body>x</body></html>",
            id="leading-space-not-leaked",
        ),
        pytest.param(
            "<!doctype html><!doctype><!--c-->",
            "<!DOCTYPE html><!--c--><html><head></head><body></body></html>",
            id="comment-stays-before-html",
        ),
    ],
)
def test_stray_doctype_after_initial_is_ignored(html: str, expected: str) -> None:
    # a DOCTYPE in any insertion mode other than "initial" is a parse error, ignored without
    # changing the insertion mode, so a second DOCTYPE must not advance the parser into "in body"
    assert parse(html).html == expected


@pytest.mark.parametrize("element", ["textarea", "title", "xmp"])
def test_rawtext_in_table_restores_table_mode(element: str) -> None:
    # a fostered RCDATA/RAWTEXT element's end tag must return to "in table", not "in body",
    # or the following rows are dropped and trailing text lands directly in the table
    doc = _doc(f"<table><{element}></{element}><tr><td>x")
    assert doc == (
        f"| <html>\n|   <head>\n|   <body>\n|     <{element}>\n|     <table>\n"
        '|       <tbody>\n|         <tr>\n|           <td>\n|             "x"'
    )


@pytest.mark.parametrize(
    "html",
    [
        pytest.param("</x><!--c-->", id="stray-unknown-end-tag"),
        pytest.param("</div><!--c-->", id="stray-known-end-tag"),
    ],
)
def test_before_html_ignored_end_tag_keeps_comment_at_document_level(html: str) -> None:
    # in "before html", an "any other end tag" is ignored without opening <html>, so a
    # following comment stays a Document-level child before <html> rather than nested inside it
    assert _doc(html) == "| <!-- c -->\n| <html>\n|   <head>\n|   <body>"


@pytest.mark.parametrize(
    "html",
    [
        pytest.param("</head>x", id="head"),
        pytest.param("</body>x", id="body"),
        pytest.param("</html>x", id="html"),
    ],
)
def test_before_html_allowed_end_tag_opens_html(html: str) -> None:
    # head/body/html/br end tags in "before html" act as "anything else": they open
    # <html> and are reprocessed, so following text lands in the body
    assert _doc(html) == '| <html>\n|   <head>\n|   <body>\n|     "x"'


def test_before_html_end_br_synthesizes_br() -> None:
    # </br> in "before html" also acts as "anything else" and is later turned into a <br>
    assert _doc("</br>x") == '| <html>\n|   <head>\n|   <body>\n|     <br>\n|     "x"'


@pytest.mark.parametrize(
    ("html", "context", "needle"),
    [
        pytest.param("</li>x", "div", '| "x"', id="end-li-no-scope"),
        pytest.param("</dd>y", "div", '| "y"', id="end-dd-no-scope"),
        pytest.param("<frame><frameset></frameset>", "frameset", "<frame>", id="frameset-context"),
        pytest.param("<option>a<select><option>b", "select", "<option>", id="select-ignores-nested-select"),
        # in a select-context fragment a second optgroup/option pops the open one, making siblings (issue #89)
        pytest.param(
            "<optgroup>1<optgroup>2",
            "select",
            '| <optgroup>\n|   "1"\n| <optgroup>\n|   "2"',
            id="select-optgroup-pops",
        ),
        pytest.param(
            "<option>1<option>2", "select", '| <option>\n|   "1"\n| <option>\n|   "2"', id="select-option-pops"
        ),
        # a non-select-context fragment keeps an optgroup without the select-mode pop
        pytest.param("<optgroup>x", "div", '| <optgroup>\n|   "x"', id="optgroup-in-non-select-fragment"),
        # an hr in a select-context fragment pops the open option, landing at select level (issue #94)
        pytest.param(
            "<option>a<hr><option>b",
            "select",
            '| <option>\n|   "a"\n| <hr>\n| <option>\n|   "b"',
            id="select-hr-pops-option",
        ),
        # an hr in a non-select-context fragment is inserted without the select-mode pop
        pytest.param("<hr>", "div", "| <hr>", id="hr-in-non-select-fragment"),
        pytest.param("  <col>x", "colgroup", "<col>", id="colgroup-whitespace-then-content"),
        pytest.param("<select></select><td>next", "tr", "<td>", id="select-in-cell-resets"),
        pytest.param("<table></table>x", "td", '"x"', id="reset-table-close-td-context"),
        pytest.param("<table></table>x", "head", '"x"', id="reset-table-close-head-context"),
        pytest.param("<table></table>x", "div", '"x"', id="reset-table-close-default-context"),
        pytest.param("   ", "title", '| "   "', id="title-whitespace-text"),
        pytest.param("", "title", "", id="empty-fragment-serializes-empty"),
        pytest.param("x", "a" * 40, '"x"', id="context-name-longer-than-buffer"),
        pytest.param("<br>", "svg", "<br>", id="breakout-stops-at-fragment-root"),
        # an html-context fragment starts in "before head" and synthesizes the
        # implicit head and body at EOF even when no token forces them (issue #42)
        pytest.param("", "html", "| <head>\n| <body>", id="html-fragment-empty-synthesizes-head-body"),
        pytest.param("<title>t</title>", "html", '|     "t"\n| <body>', id="html-fragment-head-only-adds-body"),
        pytest.param("<!--c-->", "html", "| <!-- c -->\n| <head>\n| <body>", id="html-fragment-comment-then-head-body"),
        # foster parenting in a table-section fragment context, where no <table> is on
        # the stack, fosters out to the fragment root rather than nesting (issue #55)
        pytest.param("<tr><li>x", "tbody", "| <tr>\n| <li>", id="foster-out-of-tbody-fragment"),
        pytest.param("<tr><li>x", "table", "|   <tr>\n| <li>", id="foster-out-of-table-fragment"),
        # a template context starts in "in template" mode, so table-section start tags
        # build their elements instead of dropping to text (issue #95)
        pytest.param("<td>x", "template", '| <td>\n|   "x"', id="template-fragment-td"),
        pytest.param("<col>", "template", "| <col>", id="template-fragment-col"),
        pytest.param("<tr><td>y", "template", "| <tr>\n|   <td>", id="template-fragment-tr-td"),
        pytest.param("<caption>c", "template", '| <caption>\n|   "c"', id="template-fragment-caption"),
        pytest.param("<tbody><tr><td>z", "template", "| <tbody>\n|   <tr>", id="template-fragment-tbody"),
        pytest.param("<thead><tr><th>h", "template", "| <thead>\n|   <tr>", id="template-fragment-thead"),
        # ordinary in-body content in a template context is unaffected
        pytest.param("<p>x", "template", '| <p>\n|   "x"', id="template-fragment-plain-body"),
    ],
)
def test_fragment_paths(html: str, context: str, needle: str) -> None:
    assert needle in _frag(html, context)


def test_deeply_nested_serializers_are_iterative() -> None:
    # Each serializer (compact .html, pretty indent, #document dump) recursed one
    # C stack frame per tree level and aborted (exit 134) on a deep tree. Running
    # them on a 4k nesting under a 256 KiB stack overflows any reintroduced
    # recursion while staying cheap: compact output is linear, and the
    # depth-indented pretty/dump forms stay small at this depth.
    depth = 4_000
    source = "<div>" * depth
    captured: dict[str, str] = {}

    def run() -> None:
        document = parse(source)
        captured["compact"] = document.html
        captured["pretty"] = document.serialize(Html(layout=Indent(2)))
        captured["dump"] = _html._parse_tree(source)

    previous = threading.stack_size(256 * 1024)
    try:
        worker = threading.Thread(target=run)
        worker.start()
        worker.join()
    finally:
        threading.stack_size(previous)
    assert captured["compact"].count("<div>") == depth
    assert captured["compact"].count("</div>") == depth
    assert captured["pretty"].count("<div>") == depth
    assert captured["dump"].count("<div>") == depth


# eight+ BMP (UCS-2) code points and four+ astral (UCS-4) code points, so a run
# of them is long enough to reach the vector block loop and leave a scalar tail
CJK = "東京都港区六本木ヒルズ森タワー"  # UCS-2
EMOJI = "😀🎉🚀🐍🌟🔥💡"  # UCS-4


def _body_text(html: str) -> str:
    """The serialized document tree for a whole-document parse."""
    return _html._parse_tree(html).rstrip("\n")


@pytest.mark.parametrize(
    ("html", "needle"),
    [
        pytest.param(f"<p>{CJK}</p>", f'"{CJK}"', id="ucs2-text-run"),
        pytest.param(f"<p>{EMOJI}</p>", f'"{EMOJI}"', id="ucs4-text-run"),
        pytest.param(f"<p>{CJK}&amp;{CJK}</p>", f'"{CJK}&{CJK}"', id="ucs2-text-with-ref"),
        pytest.param(f"<!--{CJK}-->", f"<!-- {CJK} -->", id="ucs2-comment"),
        pytest.param(f"<!--{EMOJI}-->", f"<!-- {EMOJI} -->", id="ucs4-comment"),
        pytest.param(f'<p title="{CJK}{EMOJI}">x</p>', f'title="{CJK}{EMOJI}"', id="double-quoted-attr"),
        # single quotes serialize back as double quotes
        pytest.param(f"<p title='{EMOJI}{CJK}'>x</p>", f'title="{EMOJI}{CJK}"', id="single-quoted-attr"),
        # an ASCII-led name carrying wide chars is a non-1-byte buffer, so it never interns to a
        # known atom and stays a plain element (a bare "<東" is not a tag - names must open ASCII)
        pytest.param(f"<a{CJK}>x</a{CJK}>", f"<a{CJK}>", id="non-ascii-tag-name"),
        # title is RCDATA, style is RAWTEXT; both take the wide run-scan
        pytest.param(f"<title>{CJK}{EMOJI}</title>", CJK, id="rcdata-wide-run"),
        pytest.param(f"<style>{EMOJI}{CJK}</style>", EMOJI, id="rawtext-wide-run"),
        # plaintext consumes the rest of the input through the wide run-scan
        pytest.param(f"<plaintext>{CJK}{EMOJI}", f"{CJK}{EMOJI}", id="plaintext-wide-run"),
    ],
)
def test_wide_character_data(html: str, needle: str) -> None:
    assert needle in _body_text(html)


@pytest.mark.parametrize(
    ("html", "present", "absent"),
    [
        # a non-1-byte attribute name takes the per-character copy path; the element and value
        # still parse and the trailing text lands in the body
        pytest.param(f'<p {CJK}="1">x</p>', ['="1"', '"x"'], [], id="non-ascii-attr-name"),
        # a CR in wide input forces the copy-and-normalize path; the CR becomes LF
        pytest.param(f"<pre>{CJK}\r\n{EMOJI}</pre>", [CJK], ["\r"], id="wide-carriage-return"),
        # a NUL in wide input sets the document NUL flag and is dropped from text
        pytest.param(f"<p>{CJK}\x00{EMOJI}</p>", [f"{CJK}{EMOJI}"], ["\x00"], id="wide-nul"),
    ],
)
def test_wide_present_and_absent(html: str, present: list[str], absent: list[str]) -> None:
    out = _body_text(html)
    assert all(needle in out for needle in present)
    assert all(needle not in out for needle in absent)


_DOCUMENT_CASES = [
    pytest.param(
        "<svg></p><foo>",
        "| <html>\n|   <head>\n|   <body>\n|     <svg svg>\n|     <p>\n|     <foo>",
        id="svg-end-p",
    ),
    pytest.param(
        "<svg></br><foo>",
        "| <html>\n|   <head>\n|   <body>\n|     <svg svg>\n|     <br>\n|     <foo>",
        id="svg-end-br",
    ),
    pytest.param(
        "<math></p><foo>",
        "| <html>\n|   <head>\n|   <body>\n|     <math math>\n|     <p>\n|     <foo>",
        id="math-end-p",
    ),
    pytest.param(
        "<math></br><foo>",
        "| <html>\n|   <head>\n|   <body>\n|     <math math>\n|     <br>\n|     <foo>",
        id="math-end-br",
    ),
]

_FRAGMENT_CASES = [
    pytest.param("<svg></p><foo>", "div", "| <svg svg>\n| <p>\n| <foo>", id="div-svg-end-p"),
    pytest.param("<svg></br><foo>", "div", "| <svg svg>\n| <br>\n| <foo>", id="div-svg-end-br"),
    pytest.param("</p><foo>", "svg svg", "| <p>\n| <svg foo>", id="svg-root-end-p"),
    pytest.param("</br><foo>", "svg svg", "| <br>\n| <svg foo>", id="svg-root-end-br"),
]


@pytest.mark.parametrize(("data", "expected"), _DOCUMENT_CASES)
def test_foreign_end_tag_breaks_out(data: str, expected: str) -> None:
    assert _html._parse_tree(data).rstrip("\n") == expected


@pytest.mark.parametrize(("data", "context", "expected"), _FRAGMENT_CASES)
def test_foreign_end_tag_in_fragment(data: str, context: str, expected: str) -> None:
    assert _html._parse_fragment(data, context).rstrip("\n") == expected


@pytest.mark.parametrize("distinct", [pytest.param(True, id="distinct-values"), pytest.param(False, id="same-values")])
def test_formatting_duplicate_scan_keeps_all_source_elements(*, distinct: bool) -> None:
    values: Final = [str(index) if distinct else "same" for index in range(256)]
    source: Final = "".join(f'<b title="{value}">' for value in values) + "a" + "</b>" * 256
    assert [(element.attrs["title"], element.text) for element in parse(source).find_all("b")] == [
        (value, "a") for value in values
    ]


@pytest.mark.parametrize(
    ("other", "attributes"),
    [
        pytest.param('title="other"', {"title": "other"}, id="different-value"),
        pytest.param('id="same"', {"id": "same"}, id="different-name"),
        pytest.param('title="else"', {"title": "else"}, id="same-length-value"),
        pytest.param("", {}, id="different-attribute-count"),
    ],
)
def test_formatting_duplicate_limit_keeps_distinct_attributes(other: str, attributes: dict[str, str]) -> None:
    source: Final = '<p><b title="same">' + f"<b {other}>" + '<b title="same">' * 3 + "one</p>two"
    assert [(element.text, dict(element.attrs)) for element in parse(source).find_all("b")] == [
        ("one", {"title": "same"}),
        ("one", attributes),
        *[("one", {"title": "same"})] * 3,
        ("two", attributes),
        *[("two", {"title": "same"})] * 3,
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "<b><object><i>one</i></object><b>two",
            "<b><object><i>one</i></object><b>two</b></b>",
            id="outer-formatting-across-object-marker",
        ),
        pytest.param(
            "<p><b><i><b><b><b>one</p>two",
            "<p><b><i><b><b><b>one</b></b></b></i></b></p><i><b><b><b>two</b></b></b></i>",
            id="different-formatting-name",
        ),
    ],
)
def test_formatting_duplicate_limit_preserves_nested_structure(source: str, expected: str) -> None:
    root: Final = parse(source).find("body")
    assert root is not None
    assert root.inner_html == expected


def test_formatting_duplicate_checks_first_of_multiple_attributes() -> None:
    source: Final = (
        '<p><b title="same" lang="en"><b title="other" lang="en">' + '<b title="same" lang="en">' * 3 + "one</p>two"
    )
    assert [(element.text, dict(element.attrs)) for element in parse(source).find_all("b")] == [
        ("one", {"title": "same", "lang": "en"}),
        ("one", {"title": "other", "lang": "en"}),
        *[("one", {"title": "same", "lang": "en"})] * 3,
        ("two", {"title": "other", "lang": "en"}),
        *[("two", {"title": "same", "lang": "en"})] * 3,
    ]


@pytest.mark.parametrize("depth", [pytest.param(1, id="shallow"), pytest.param(256, id="deep")])
def test_formatting_reconstruction_keeps_persistent_ancestor(depth: int) -> None:
    source: Final = "<b>" + "<span>" * depth + "<samp>text</samp>" * 100 + "</span>" * depth + "</b>"
    root: Final = parse(source).find("b")
    assert root is not None
    assert (root.text, [element.text for element in root.find_all("samp")]) == ("text" * 100, ["text"] * 100)


@pytest.mark.parametrize("depth", [pytest.param(1, id="shallow"), pytest.param(100, id="deep")])
def test_formatting_reconstruction_after_ancestor_removal(depth: int) -> None:
    source: Final = "<b>" + "<span>" * depth + "first</b>second" + "</span>" * depth + "<i>third</i>"
    root: Final = parse_fragment(source, "div")
    assert (root.text, [element.text for element in root.find_all("b")]) == ("firstsecondthird", ["first"])


@pytest.mark.parametrize(
    ("source", "bold", "italic"),
    [
        pytest.param("<p><b>one</p>two", ["one", "two"], [], id="stack-pop"),
        pytest.param("<b><i>one</b>two</i>", ["one"], ["one", "two"], id="adoption-replacement"),
        pytest.param("<p><b>one</p><div>two</div>three", ["one", "two", "three"], [], id="stack-slot-reuse"),
        pytest.param("<table><tr><td><b>one</td><td>two</td></tr></table>three", ["one"], [], id="cell-marker"),
    ],
)
@pytest.mark.parametrize("locations", [pytest.param(False, id="no-locations"), pytest.param(True, id="locations")])
def test_formatting_reconstruction_stack_changes(
    source: str, bold: list[str], italic: list[str], *, locations: bool
) -> None:
    root: Final = parse_fragment(source, "div", source_locations=locations)
    assert ([element.text for element in root.find_all("b")], [element.text for element in root.find_all("i")]) == (
        bold,
        italic,
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<p><b><i>one</p>two", [("one", ["one"]), ("two", ["two"])], id="both-formatting-elements-popped"),
        pytest.param("<b><p><i>one</p>two</b>", [("onetwo", ["one", "two"])], id="outer-formatting-element-open"),
        pytest.param("<table><tr><td><p><b>one</p>two", [("one", []), ("two", [])], id="cell-scope-marker"),
    ],
)
def test_formatting_reconstruction_reopens_nested_elements(source: str, expected: list[tuple[str, list[str]]]) -> None:
    root: Final = parse_fragment(source, "div")
    assert [(bold.text, [italic.text for italic in bold.find_all("i")]) for bold in root.find_all("b")] == expected


@pytest.mark.parametrize(
    ("markup", "parent_tag", "expected"),
    [
        pytest.param("<select><keygen>", "select", ["keygen"], id="select"),
        pytest.param("<keygen><span>", "body", ["keygen", "span"], id="body"),
    ],
)
def test_keygen_document_children(markup: str, parent_tag: str, expected: list[str]) -> None:
    parent = parse(markup).find(parent_tag)
    assert parent is not None
    assert [child.tag for child in parent.children if isinstance(child, Element)] == expected


def test_keygen_and_option_are_siblings_in_select_fragment() -> None:
    fragment = parse_fragment("<keygen><option>", "select")
    assert [child.tag for child in fragment.children if isinstance(child, Element)] == ["keygen", "option"]


@pytest.mark.parametrize(
    ("markup", "target", "data"),
    [
        pytest.param("<?one>", "one", "", id="empty-data"),
        pytest.param("<?one value?>", "one", "value", id="data"),
        pytest.param("<svg><?one value?></svg>", "one", "value", id="foreign-content"),
        pytest.param("<table><?one value?></table>", "one", "value", id="table"),
    ],
)
def test_html_parse_builds_processing_instruction(markup: str, target: str, data: str) -> None:
    node = _processing_instruction(markup)
    assert (node.target, node.data) == (target, data)


@pytest.mark.parametrize(
    ("markup", "parent_tag"),
    [
        pytest.param("<?pi><!doctype html>", None, id="initial"),
        pytest.param("<!doctype html><?pi><html>", None, id="before-html"),
        pytest.param("<html><?pi><head>", "html", id="before-head"),
        pytest.param("<head><?pi></head>", "head", id="in-head"),
        pytest.param("<head><noscript><?pi></noscript>", "noscript", id="in-head-noscript"),
        pytest.param("</head><?pi><body>", "html", id="after-head"),
        pytest.param("<frameset><?pi>", "frameset", id="in-frameset"),
        pytest.param("<frameset></frameset><?pi>", "html", id="after-frameset"),
        pytest.param("<body><?pi>", "body", id="in-body"),
        pytest.param("<table><?pi></table>", "table", id="in-table"),
        pytest.param("<table><colgroup><?pi></colgroup></table>", "colgroup", id="in-column-group"),
        pytest.param("<body></body><?pi>", "html", id="after-body"),
        pytest.param("<body></body></html><?pi>", None, id="after-after-body"),
        pytest.param("<frameset></frameset></html><?pi>", None, id="after-after-frameset"),
        pytest.param("<svg><?pi></svg>", "svg", id="foreign-content"),
    ],
)
def test_processing_instruction_uses_comment_insertion_location(markup: str, parent_tag: str | None) -> None:
    parent = _processing_instruction(markup).parent
    if parent_tag is None:
        assert isinstance(parent, Document)
    else:
        assert isinstance(parent, Element)
        assert parent.tag == parent_tag


@pytest.mark.parametrize("target", [pytest.param("xml", id="xml"), pytest.param("XmL-StYlEsHeEt", id="stylesheet")])
def test_reserved_xml_processing_instruction_target_stays_a_comment(target: str) -> None:
    node = next(node for node in parse(f"<?{target} value?>").descendants if isinstance(node, Comment))
    assert node.data == f"?{target} value?"


@pytest.mark.parametrize(
    ("markup", "error"),
    [
        pytest.param("<?1bad>", "invalid-first-character-of-processing-instruction-target", id="first-character"),
        pytest.param("<?bad.target>", "invalid-processing-instruction-target", id="target-character"),
        pytest.param("<?xml?>", "disallowed-processing-instruction-target", id="reserved-target"),
        pytest.param("<?", "eof-in-processing-instruction", id="open-eof"),
        pytest.param("<?pi", "eof-in-processing-instruction", id="target-eof"),
        pytest.param("<?pi data", "eof-in-processing-instruction", id="data-eof"),
        pytest.param("<?pi data?", "eof-in-processing-instruction", id="questionable-eof"),
    ],
)
def test_processing_instruction_parse_error(markup: str, error: str) -> None:
    assert [item.code for item in parse(markup).errors] == [error]


def test_processing_instruction_data_keeps_markup_and_null() -> None:
    document = parse("<body><?pi &amp;<tag\0?>")
    node = next(node for node in document.descendants if isinstance(node, ProcessingInstruction))
    assert node.data == "&amp;<tag\0"
    assert document.errors == []


def test_processing_instruction_in_fragment_and_template() -> None:
    fragment = parse_fragment("<template><?pi data></template><?tail>", "div")
    assert [(node.target, node.data) for node in fragment.descendants if isinstance(node, ProcessingInstruction)] == [
        ("pi", "data"),
        ("tail", ""),
    ]


def test_parsed_processing_instruction_clone_preserves_fields() -> None:
    node = _processing_instruction("<body><?pi old>")
    clone = copy.copy(node)
    assert (clone.target, clone.data) == ("pi", "old")


def test_parsed_processing_instruction_extract_preserves_fields() -> None:
    node = _processing_instruction("<body><?pi old>")
    host = Element("div")
    host.append(node.extract())
    assert (node.target, node.data, node.parent) == ("pi", "old", host)


def test_selectedcontent_clone_preserves_processing_instruction() -> None:
    selectedcontent = parse("<select><button><selectedcontent></button><option><?pi value?>").find("selectedcontent")
    assert isinstance(selectedcontent, Element)
    (node,) = selectedcontent.children
    assert isinstance(node, ProcessingInstruction)
    assert (node.target, node.data) == ("pi", "value")


def test_parsed_processing_instruction_serializes_and_minifies() -> None:
    document = parse("<body><?pi data?></body>")
    assert (document.html, document.serialize(Html(layout=Minify()))) == (
        "<html><head></head><body><?pi data></body></html>",
        "<body><?pi data>",
    )


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<head><?pi></head><body>x", id="head"),
        pytest.param("<body>x</body><?pi>", id="after-body"),
        pytest.param("<body>x</body></html><?pi>", id="after-html"),
    ],
)
def test_minifying_processing_instruction_preserves_insertion_location(markup: str) -> None:
    document = parse(markup)
    assert parse(document.serialize(Html(layout=Minify()))).equals(document)


def _processing_instruction(markup: str) -> ProcessingInstruction:
    return next(node for node in parse(markup).descendants if isinstance(node, ProcessingInstruction))


@pytest.mark.parametrize(
    ("markup", "body_html"),
    [
        pytest.param(
            "<nobr><table><marquee></table><nobr>",
            "<body><nobr><marquee></marquee><table></table></nobr><nobr></nobr></body>",
            id="table-and-open-marquee",
        ),
        pytest.param(
            "<nobr><table></table><nobr>",
            "<body><nobr><table></table></nobr><nobr></nobr></body>",
            id="table",
        ),
        pytest.param(
            "<nobr><marquee></marquee><nobr>",
            "<body><nobr><marquee></marquee></nobr><nobr></nobr></body>",
            id="marquee",
        ),
        pytest.param("<nobr>x<nobr>y", "<body><nobr>x</nobr><nobr>y</nobr></body>", id="repeated-nobr"),
    ],
)
def test_nobr_adoption_builds_siblings(markup: str, body_html: str) -> None:
    body = parse(markup).find("body")
    assert body is not None
    assert body.html == body_html


@pytest.fixture(
    params=[
        pytest.param("<p><b title='old' lang='en'>one</p>two", id="reconstruction"),
        pytest.param("<b title='old' lang='en'><p>one</b>two", id="adoption"),
        pytest.param(
            "<select><selectedcontent></selectedcontent><option><b title='old' lang='en'>x</b></option></select>",
            id="selectedcontent",
        ),
    ]
)
def cloned_elements(request: pytest.FixtureRequest) -> list[Element]:
    return parse_fragment(request.param).select("b")


@pytest.mark.parametrize("changed", [pytest.param(0, id="first"), pytest.param(1, id="second")])
def test_cloned_attribute_update_is_independent(cloned_elements: list[Element], changed: int) -> None:
    cloned_elements[changed].attrs["title"] = "new"
    assert [element.attr("title") for element in cloned_elements] == (
        ["new", "old"] if changed == 0 else ["old", "new"]
    )


@pytest.mark.parametrize("changed", [pytest.param(0, id="first"), pytest.param(1, id="second")])
def test_cloned_attribute_removal_is_independent(cloned_elements: list[Element], changed: int) -> None:
    del cloned_elements[changed].attrs["title"]
    assert [element.attrs.copy() for element in cloned_elements] == (
        [{"lang": "en"}, {"title": "old", "lang": "en"}]
        if changed == 0
        else [{"title": "old", "lang": "en"}, {"lang": "en"}]
    )


@pytest.mark.parametrize("count", [pytest.param(1, id="small"), pytest.param(32, id="bulk")])
def test_sanitizing_reconstructed_attributes_keeps_unique_names(count: int) -> None:
    attributes: Final = " ".join(f'data-{index}="{index}"' for index in range(count))
    assert (
        sanitize(
            f'<p><b onclick="bad()" {attributes}>one</p>two',
            Policy(tags=frozenset({"p", "b"}), attributes={"b": frozenset({"*"})}),
        )
        == f"<p><b {attributes}>one</b></p><b {attributes}>two</b>"
    )


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("a", id="ascii"),
        pytest.param("é", id="latin1"),
        pytest.param("名", id="ucs2"),
        pytest.param("😀", id="ucs4"),
    ],
)
@pytest.mark.parametrize("fragment", [pytest.param(False, id="document"), pytest.param(True, id="fragment")])
@pytest.mark.parametrize("locations", [pytest.param(False, id="no-locations"), pytest.param(True, id="locations")])
def test_normalized_text_survives_temporary_input_and_detachment(text: str, *, fragment: bool, locations: bool) -> None:
    paragraph: Final = (
        parse_fragment(f"<p>{text}\r\n{text}\r{text}</p><p>\0</p>", "div", source_locations=locations)
        if fragment
        else parse(f"<p>{text}\r\n{text}\r{text}</p><p>\0</p>", source_locations=locations)
    ).find("p")
    assert paragraph is not None
    paragraph.extract()
    assert (paragraph.parent, paragraph.text, paragraph.html) == (
        None,
        f"{text}\n{text}\n{text}",
        f"<p>{text}\n{text}\n{text}</p>",
    )


@pytest.mark.parametrize("tag", ["script", "style", "textarea"])
def test_normalized_text_keeps_raw_text_newlines(tag: str) -> None:
    element: Final = parse(f"<{tag}>left\r\nright\r</{tag}>").find(tag)
    assert element is not None
    assert element.text == "left\nright\n"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("a", id="ascii"),
        pytest.param("é", id="latin1"),
        pytest.param("名", id="ucs2"),
        pytest.param("😀", id="ucs4"),
    ],
)
@pytest.mark.parametrize("locations", [pytest.param(False, id="no-locations"), pytest.param(True, id="locations")])
def test_nul_text_preserves_clean_runs_and_source_positions(text: str, *, locations: bool) -> None:
    clean: Final = text * 80
    source: Final = (
        f"<p>first\0{text}</p>\n<p>{clean}</p><script>{clean}</script>"
        f"<textarea>\n{clean}</textarea><svg>{text}\0{text}</svg>"
    )
    document: Final = parse(source, positions=locations, source_locations=locations)
    assert [(element.tag, element.text) for element in document.select("p, script, textarea, svg")] == [
        ("p", f"first{text}"),
        ("p", clean),
        ("script", clean),
        ("textarea", clean),
        ("svg", f"{text}\ufffd{text}"),
    ]
    assert document.find_all("p")[1].position == ((2, 0) if locations else None)


@pytest.mark.parametrize("tag", ["script", "style", "textarea"])
def test_nul_text_keeps_raw_text_replacement(tag: str) -> None:
    root: Final = parse(f"<p>before\0after</p><{tag}>left\0right</{tag}><p>clean</p>")
    element: Final = root.find(tag)
    assert element is not None
    assert element.text == "left\ufffdright"


@pytest.mark.parametrize("depth", [pytest.param(1, id="shallow"), pytest.param(256, id="deep")])
def test_scope_repeated_ignored_end_tags(depth: int) -> None:
    source: Final = "<div>" + "<span>" * depth + "before" + "</address>" * 1000 + "after" + "</span>" * depth + "</div>"
    root: Final = parse(source).find("div")
    assert root is not None
    assert (root.text, len(root.find_all("span"))) == ("beforeafter", depth)


@pytest.mark.parametrize(
    ("source", "tag", "expected"),
    [
        pytest.param("<div></address><address>inside</address>outside</div>", "address", ["inside"], id="push"),
        pytest.param(
            "<div><address>inside</address>outside</address>tail</div>", "div", ["insideoutsidetail"], id="pop"
        ),
        pytest.param("<div><span>before</address></div>after", "div", ["before"], id="different-atom"),
    ],
)
def test_scope_reuse_after_stack_changes(source: str, tag: str, expected: list[str]) -> None:
    assert [element.text for element in parse(source).find_all(tag)] == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "<b><i><p>x</b>y",
            "<html><head></head><body><b><i></i></b><i><p><b>x</b>y</p></i></body></html>",
            id="inner-adoption-clone",
        ),
        pytest.param(
            "<head></head><template><p>x</p></template><p>y</p>",
            "<html><head><template><p>x</p></template></head><body><p>y</p></body></html>",
            id="after-head-template",
        ),
    ],
)
def test_scope_stack_replacement_preserves_tree(source: str, expected: str) -> None:
    assert parse(source).serialize() == expected


def test_default_parses_noscript_content_as_markup() -> None:
    noscript = parse("<body><noscript><b>x</b></noscript>").find("noscript")
    assert noscript is not None
    assert noscript.find("b") is not None  # <b> is a real child element, not raw text


def test_scripting_keeps_noscript_content_as_raw_text() -> None:
    noscript = parse("<body><noscript><b>x</b></noscript>", scripting=True).find("noscript")
    assert noscript is not None
    children = list(noscript.children)
    assert len(children) == 1
    assert isinstance(children[0], Text)
    assert noscript.text == "<b>x</b>"


def test_scripting_moves_in_head_noscript_content_to_raw_text() -> None:
    # in head, a scripting-off noscript wraps only metadata and its markup escapes to
    # the body; with scripting on it is raw text and keeps the <iframe> as literal text
    head_noscript = parse("<noscript><iframe></noscript>X", scripting=True).find("noscript")
    assert head_noscript is not None
    assert head_noscript.text == "<iframe>"


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param(None, id="compact"),
        pytest.param(Indent(2), id="pretty"),
        pytest.param(Minify(), id="minify"),
    ],
)
@pytest.mark.parametrize(
    ("scripting", "fragment"),
    [
        # off: noscript is a normal element, its "<" text escapes (pretty may reflow it)
        pytest.param(False, "1 &lt; 2", id="off-escaped"),
        # on: noscript is raw text, kept verbatim on one line in every layout
        pytest.param(True, "<noscript>1 < 2</noscript>", id="on-raw"),
    ],
)
def test_every_layout_honors_scripting_for_noscript(
    layout: Indent | Minify | None, fragment: str, *, scripting: bool
) -> None:
    noscript = parse("<body><noscript>1 < 2</noscript>", scripting=scripting).find("noscript")
    assert noscript is not None
    assert fragment in noscript.serialize(Html(layout=layout))


def test_scripting_roundtrip_is_idempotent() -> None:
    once = parse("<noscript><iframe></noscript>X", scripting=True).html
    assert parse(once, scripting=True).html == once


def test_fragment_default_parses_noscript_content_as_markup() -> None:
    noscript = parse_fragment("<noscript><b>x</b></noscript>", "div").find("noscript")
    assert noscript is not None
    assert noscript.find("b") is not None


def test_fragment_scripting_keeps_noscript_content_as_raw_text() -> None:
    noscript = parse_fragment("<noscript><b>x</b></noscript>", "div", scripting=True).find("noscript")
    assert noscript is not None
    assert noscript.text == "<b>x</b>"


def test_fragment_noscript_context_parses_as_raw_text_under_scripting() -> None:
    children = list(parse_fragment("<b>x</b>", "noscript", scripting=True).children)
    assert len(children) == 1
    assert isinstance(children[0], Text)
    assert children[0].data == "<b>x</b>"


def test_inner_html_inherits_the_trees_scripting_flag() -> None:
    div = parse("<div></div>", scripting=True).find("div")
    assert div is not None
    div.set_inner_html("<noscript><b>x</b></noscript>")
    noscript = div.find("noscript")
    assert noscript is not None
    assert noscript.text == "<b>x</b>"  # raw text, inheriting scripting from the tree


def test_inner_html_off_by_default_keeps_noscript_markup() -> None:
    div = parse("<div></div>").find("div")
    assert div is not None
    div.set_inner_html("<noscript><b>x</b></noscript>")
    noscript = div.find("noscript")
    assert noscript is not None
    assert noscript.find("b") is not None


def test_unacknowledged_self_closing_start_tag_reports_the_token_position() -> None:
    assert [(error.code, error.line, error.col) for error in parse("<ul><li><div id='foo'/>A</li></ul>").errors] == [
        ("non-void-html-element-start-tag-with-trailing-solidus", 1, 8)
    ]


def test_document_ignores_the_slash_on_a_non_void_html_element() -> None:
    document = parse("<ul><li><div id='foo'/>A</li></ul>")
    element = document.find("div")
    assert element is not None
    assert element.text == "A"


def test_fragment_ignores_the_slash_on_a_non_void_html_element() -> None:
    fragment = parse_fragment("<div/>A", "body")
    element = fragment.find("div")
    assert element is not None
    assert element.text == "A"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<br/>", id="html-void"),
        pytest.param("<image/>", id="image-alias"),
        pytest.param("<svg/>", id="svg-root"),
        pytest.param("<math/>", id="mathml-root"),
        pytest.param("<svg><path/></svg>", id="svg-child"),
        pytest.param("<math><mrow/></math>", id="mathml-child"),
    ],
)
def test_acknowledged_self_closing_start_tag_reports_no_error(markup: str) -> None:
    assert parse(markup).errors == []


def test_html_integration_point_does_not_acknowledge_a_non_void_start_tag() -> None:
    assert [error.code for error in parse("<math><mtext><ms/>X</mtext></math>").errors] == [
        "non-void-html-element-start-tag-with-trailing-solidus"
    ]


def test_html_integration_point_ignores_the_slash() -> None:
    document = parse("<math><mtext><ms/>X</mtext></math>")
    element = document.find("ms")
    assert element is not None
    assert element.text == "X"


_PATHOLOGICAL: Final = 20000
_DEEP: Final = 1_200


def _max_element_depth(node: Element | Document) -> int:
    deepest = 0
    stack: list[tuple[object, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        here = depth + 1 if isinstance(current, Element) else depth
        deepest = max(deepest, here)
        stack.extend((child, here) for child in getattr(current, "children", []) or [])
    return deepest


def _nested(tag: str, levels: int, text: str | None = "X") -> Element:
    return _nested_with_deepest(tag, levels, text)[0]


def _nested_with_deepest(tag: str, levels: int, text: str | None = "X") -> tuple[Element, Element]:
    root = Element(tag)
    node = root
    for _ in range(levels):
        child = Element(tag)
        node.append(child)
        node = child
    if text is not None:
        node.append(Text(text))
    return root, node


def test_deeply_nested_parse_caps_element_depth() -> None:
    doc = parse("<div>" * _PATHOLOGICAL)
    assert _max_element_depth(doc) <= 520  # bounded near the 512 cap, not ~20000


def test_deeply_nested_parse_keeps_every_element() -> None:
    doc = parse("<div>" * _PATHOLOGICAL)
    assert len(doc.find_all("div")) == _PATHOLOGICAL  # capped elements survive as siblings


def test_capped_parse_round_trips_through_serialization() -> None:
    doc = parse("<div>" * _PATHOLOGICAL)
    assert doc.html.count("<div>") == _PATHOLOGICAL
    assert doc.html.count("</div>") == _PATHOLOGICAL


def test_sanitize_of_deeply_nested_input_does_not_overflow() -> None:
    result = sanitize("<div>" * _PATHOLOGICAL)  # F1 repro: parse + recursive sanitize walk
    assert result.count("&lt;div&gt;") == _PATHOLOGICAL  # div is escaped, not kept, but every one survives


@pytest.mark.parametrize("levels", [pytest.param(400, id="under-cap"), pytest.param(511, id="at-cap")])
def test_nesting_below_the_cap_is_left_untouched(levels: int) -> None:
    doc = parse("<div>" * levels)
    assert _max_element_depth(doc) == levels + 2  # html + body + every div, fully nested


@pytest.mark.parametrize("source", [pytest.param("xml", id="parsed-xml"), pytest.param("mutation", id="mutation")])
def test_deep_text_and_serialization_are_complete(source: str) -> None:
    root = (
        parse_xml("<x>" * _DEEP + "bottom" + "</x>" * _DEEP).root if source == "xml" else _nested("x", _DEEP, "bottom")
    )
    assert isinstance(root, Element)
    expected_open_count = _DEEP if source == "xml" else _DEEP + 1
    assert (root.text, root.html.count("<x>"), "bottom" in root.serialize(Html(layout=Indent(1)))) == (
        "bottom",
        expected_open_count,
        True,
    )


def test_find_text_reads_a_deep_programmatic_tree() -> None:
    root = _nested("x", _DEEP, "bottom")
    assert root.find("x", text="bottom") is not None


def test_css_has_reads_a_deep_programmatic_tree() -> None:
    root, deepest = _nested_with_deepest("x", _DEEP, None)
    deepest.attrs["class"] = "target"
    assert root.matches(":has(.target)")
    assert root.select_one("x:has(.target)") is not None


@pytest.mark.parametrize(
    ("method", "args"),
    [
        pytest.param("to_markdown", (), id="markdown"),
        pytest.param("to_text", (), id="text"),
        pytest.param("to_annotated_text", ({"div": ["deep"]},), id="annotated-text"),
    ],
)
def test_recursive_renderers_reject_a_deep_tree_before_output(method: str, args: tuple[object, ...]) -> None:
    root = _nested("div", _DEEP)
    with pytest.raises(RecursionError, match=rf"{method}\(\).*1024"):
        getattr(root, method)(*args)


def test_recursive_renderers_accept_the_last_supported_depth() -> None:
    root = _nested("b", 1_022)
    assert "X" in root.to_markdown()
    assert root.to_text() == "X"


@pytest.mark.parametrize("duplicate", [pytest.param(copy.copy, id="copy"), pytest.param(copy.deepcopy, id="deepcopy")])
def test_clone_walk_copies_a_deep_tree(duplicate: Callable[[Element], Element]) -> None:
    root = _nested("x", _DEEP, "bottom")
    clone = duplicate(root)
    assert clone.text == "bottom"
    assert clone.equals(root)


def test_clone_walk_copies_deep_siblings() -> None:
    root, deepest = _nested_with_deepest("x", _DEEP, None)
    deepest.append(Element("a"))
    deepest.append(Element("b"))
    clone = copy.copy(root)
    assert (clone is not root, clone.html) == (True, root.html)


def test_append_copies_a_deep_foreign_tree() -> None:
    root = Element("root")
    child = _nested("x", _DEEP, "bottom")
    root.append(child)
    assert root.text == "bottom"
    assert child.text == "bottom"


def test_normalize_walk_reaches_deep_text() -> None:
    root, deepest = _nested_with_deepest("x", _DEEP, None)
    deepest.append(Text("a"))
    deepest.append(Text(""))
    deepest.append(Text("b"))
    root.normalize()
    assert root.text == "ab"


def test_normalize_walk_merges_text_within_the_cap() -> None:
    element = Element("div")
    element.append(Text("a"))
    element.append(Text("b"))
    element.normalize()
    assert element.text == "ab"


def test_readability_reaches_a_deep_programmatic_tree_before_render_preflight() -> None:
    root = _nested("x", _DEEP, None)
    root.append(Element("p", None, [Text("A long article sentence, " * 20)]))
    assert root.main_content() == root
    with pytest.raises(RecursionError, match=r"to_text\(\).*1024"):
        root.main_text()
    with pytest.raises(RecursionError, match=r"to_text\(\).*1024"):
        root.article()


@pytest.mark.parametrize("method", ["clone_contents", "extract_contents", "delete_contents"])
@pytest.mark.parametrize("deep_boundary", [pytest.param("start", id="deep-start"), pytest.param("end", id="deep-end")])
def test_range_rejects_a_deep_partial_boundary_before_mutation(method: str, deep_boundary: str) -> None:
    root, deepest = _nested_with_deepest("x", _DEEP, None)
    text_node = Text("bottom")
    deepest.append(text_node)
    if deep_boundary == "start":
        selection = Range(text_node, 0)
        selection.set_end(root, 1)
    else:
        selection = Range(root, 0)
        selection.set_end(text_node, 0)
    before = root.html
    with pytest.raises(RecursionError, match=rf"{method}\(\).*400"):
        getattr(selection, method)()
    assert (root.html, selection.start_container, selection.end_container) == (
        before,
        text_node if deep_boundary == "start" else root,
        root if deep_boundary == "start" else text_node,
    )


def test_structured_data_methods_accept_deep_dom_with_shallow_records() -> None:
    document = parse_xml("<x>" * _DEEP + "<item itemscope='' typeof='Thing'/>" + "</x>" * _DEEP)
    microdata = [MicrodataItem(type=None, id=None, properties={})]
    rdfa = [RdfaItem(vocab=None, type=["Thing"], resource=None, properties={})]
    assert (document.microdata(), document.rdfa(), document.structured_data()) == (
        microdata,
        rdfa,
        StructuredData(json_ld=[], microdata=microdata, opengraph={}, microformats=[], rdfa=rdfa, dublin_core={}),
    )


@pytest.mark.parametrize("method", ["microdata", "structured_data"])
def test_microdata_rejects_cyclic_itemref_graph(method: str) -> None:
    document = parse(
        "<div itemscope itemref='b'></div>"
        "<div id='b' itemprop='next' itemscope itemref='c'></div>"
        "<div id='c' itemprop='next' itemscope itemref='b'></div>"
    )
    with pytest.raises(RecursionError, match="cyclic nested item graph"):
        getattr(document, method)()


@pytest.mark.parametrize("method", ["microdata", "structured_data"])
def test_microdata_rejects_more_than_400_nested_records(method: str) -> None:
    document = parse("<div itemscope>" + "<div itemprop='next' itemscope>" * 400 + "</div>" * 401)
    with pytest.raises(RecursionError, match=r"microdata\(\).*400 nested items"):
        getattr(document, method)()


@pytest.mark.parametrize("method", ["rdfa", "structured_data"])
def test_rdfa_rejects_more_than_400_nested_records(method: str) -> None:
    document = parse("<div typeof='Thing'>" + "<div property='next' typeof='Thing'>" * 400 + "</div>" * 401)
    with pytest.raises(RecursionError, match=r"rdfa\(\).*400 nested items"):
        getattr(document, method)()


def test_deep_operations_fit_a_small_thread_stack() -> None:
    roots = [
        parse_xml("<x>" * _DEEP + "bottom" + "</x>" * _DEEP).root,
        _nested("x", _DEEP, "bottom"),
    ]

    def run() -> list[tuple[str, str, str]]:
        captured: list[tuple[str, str, str]] = []
        for root in roots:
            assert isinstance(root, Element)
            clone = copy.deepcopy(root)
            clone.normalize()
            with pytest.raises(RecursionError):
                root.to_markdown()
            assert root.matches(":has(x)")
            captured.append((root.text, clone.text, root.serialize(Html(layout=Indent(1)))))
        return captured

    previous = threading.stack_size(256 * 1024)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            captured = pool.submit(run).result()
    finally:
        threading.stack_size(previous)
    assert [(text, clone_text) for text, clone_text, _ in captured] == [("bottom", "bottom")] * 2
    assert all("bottom" in markup for _, _, markup in captured)
