"""Tree-builder paths the html5lib suite does not reach.

The conformance suite is ASCII and document-shaped, so it never drives the
private C hooks, the quirks-doctype edge identifiers, several fragment-context
reset paths, the selectedcontent cloning corners, or the zero-copy text-span
fallbacks. These target those directly through the private _html entry points.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

from turbohtml import Html, Indent, _html, parse

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
        # </br> in foreign content is not a breakout tag: like </p> it is "any other
        # end tag", so the synthesized <br> lands inside the svg (issue #63)
        pytest.param(
            "<math><mtext><svg></br>q</svg></mtext></math>",
            "<svg svg>\n|           <br>",
            id="end-br-in-foreign-inserts-svg-child",
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
        # an unmatched </p> in foreign content inserts the implied <p> inside the
        # foreign root, never as a sibling under <body> (issue #32)
        pytest.param("<svg></p>", "<svg svg>\n|       <p>", id="end-p-in-svg-inserts-child"),
        pytest.param("<math></p>", "<math math>\n|       <p>", id="end-p-in-math-inserts-child"),
        # </br> is likewise "any other end tag": the synthesized <br> lands inside the
        # foreign root rather than as a sibling under <body> (issue #63)
        pytest.param("<svg></br>", "<svg svg>\n|       <br>", id="end-br-in-svg-inserts-child"),
        pytest.param("<math></br>", "<math math>\n|       <br>", id="end-br-in-math-inserts-child"),
        # </p> in foreign content is not a breakout tag: per the spec's "any other
        # end tag" rule nothing is popped, so the implied <p> lands inside the svg
        pytest.param(
            "<math><mtext><svg></p>q</svg></mtext></math>",
            "<svg svg>\n|           <p>",
            id="end-p-in-foreign-inserts-svg-child",
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
            "<svg><foreignObject><svg></p>x", "<svg svg>\n|           <p>", id="end-p-in-foreign-not-breakout"
        ),
        pytest.param("<svg><desc><svg></br>y", "<svg svg>\n|           <br>", id="end-br-in-foreign-not-breakout"),
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
