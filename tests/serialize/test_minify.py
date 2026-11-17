from __future__ import annotations

import random
from functools import partial
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Doctype, Element, Formatter, Html, Minify, Text, parse, parse_fragment, parse_xml
from turbohtml.clean import CSSMinify, minify, strip_comments_node

if TYPE_CHECKING:
    from work_steps import WorkSteps
    from wpt_tree_corpus import WptHtmlTreeCorpus
from turbohtml.clean import JSMinify
from turbohtml.clean import Minify as CleanMinify


def test_minify_defaults_all_on() -> None:
    minify = Minify()
    assert (
        minify.collapse_whitespace,
        minify.omit_optional_tags,
        minify.unquote_attributes,
        minify.strip_comments,
    ) == (
        True,
        True,
        True,
        True,
    )


def test_minify_flags_independent() -> None:
    minify = Minify(collapse_whitespace=False, omit_optional_tags=False, unquote_attributes=False, strip_comments=False)
    assert (
        minify.collapse_whitespace,
        minify.omit_optional_tags,
        minify.unquote_attributes,
        minify.strip_comments,
    ) == (
        False,
        False,
        False,
        False,
    )


def test_minify_repr_roundtrips_through_eval() -> None:
    assert repr(Minify(omit_optional_tags=False)) == (
        "Minify(collapse_whitespace=True, omit_optional_tags=False, unquote_attributes=True, strip_comments=True, "
        "minify_js=None, minify_css=None)"
    )
    assert repr(Minify(collapse_whitespace=False, unquote_attributes=False, strip_comments=False)) == (
        "Minify(collapse_whitespace=False, omit_optional_tags=True, unquote_attributes=False, strip_comments=False, "
        "minify_js=None, minify_css=None)"
    )


def test_minify_equality_and_hash() -> None:
    assert Minify() == Minify()
    assert Minify(strip_comments=False) != Minify()
    assert hash(Minify()) == hash(Minify())
    assert hash(Minify(strip_comments=False)) != hash(Minify())


def test_minify_not_equal_to_other_type() -> None:
    assert Minify() != "Minify()"
    assert (Minify() == 3) is False


def test_minify_unorderable() -> None:
    with pytest.raises(TypeError):
        _ = Minify() < Minify()  # ty: ignore[unsupported-operator]  # ordering is unsupported on purpose


def test_minify_rejects_unknown_keyword() -> None:
    with pytest.raises(TypeError):
        Minify(unknown_flag=True)  # ty: ignore[unknown-argument]  # an unknown flag is rejected at runtime


def test_minify_rejects_positional() -> None:
    with pytest.raises(TypeError):
        Minify(True)  # ty: ignore[too-many-positional-arguments]  # ruff:ignore[boolean-positional-value-in-call]  # keyword-only


def test_serialize_without_minify_is_unchanged() -> None:
    assert parse("<p>x</p>").serialize().startswith("<html>")


def test_serialize_layout_none_is_compact() -> None:
    assert parse("<p>x").serialize(Html(layout=None)) == parse("<p>x").serialize()


def test_serialize_rejects_non_layout() -> None:
    with pytest.raises(TypeError, match="layout must be an Indent"):
        parse("<p>x").serialize(Html(layout=True))  # ty: ignore[invalid-argument-type]  # non-layout rejected


def test_encode_minify() -> None:
    assert parse("<p>a</p>").encode(options=Html(layout=Minify())) == b"<p>a"


@pytest.mark.parametrize("tag", ["script", "style", "xmp", "iframe", "noembed", "noframes"])
def test_minify_writes_raw_text_carriage_return_as_line_feed(tag: str) -> None:
    node = Element(tag, children=[Text("a\rb\r\nc")])
    assert node.serialize(Html(layout=Minify())) == f"<{tag}>a\nb\nc</{tag}>"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a   \t\n  b", "a b", id="collapse-runs-to-single-space"),
        pytest.param("<pre>  a   b  </pre>", "<pre>  a   b  </pre>", id="collapse-preserves-pre"),
        pytest.param(
            "<textarea>  a   b  </textarea>", "<textarea>  a   b  </textarea>", id="collapse-preserves-textarea"
        ),
        pytest.param("a <!--x--> b", "a b", id="collapse-merges-across-stripped-comment"),
        pytest.param("a <b>x</b> b", "a <b>x</b> b", id="collapse-keeps-space-around-element"),
        # the fused collapse still escapes &, < and > between the folded whitespace runs
        pytest.param(
            "a &  b   <  c & d > e", "a &amp; b &lt; c &amp; d &gt; e", id="collapse-escapes-specials-in-text"
        ),
        # a non-break space is not ASCII whitespace, so WHATWG escapes it rather than folding
        pytest.param("a\u00a0\u00a0b", "a&nbsp;&nbsp;b", id="collapse-escapes-nbsp-under-whatwg"),
        pytest.param("<a href='x'>t</a>", "<a href=x>t</a>", id="unquote-safe-value"),
        pytest.param("<a title='a b'>t</a>", '<a title="a b">t</a>', id="unquote-keeps-quotes-on-space"),
        pytest.param('<input disabled="">', "<input disabled>", id="unquote-empty-value-becomes-bare-name"),
        # a name keeps a leading "=", which after a bare name would open that name's value instead
        pytest.param(
            '<a title="" =q>t</a>',
            '<a title="" =q>t</a>',
            id="unquote-empty-value-stays-before-equals-name",
        ),
        pytest.param(
            '<a p="" =q="" =r s="">t</a>',
            '<a p="" =q="" =r s>t</a>',
            id="unquote-empty-value-stays-before-each-equals-name",
        ),
        pytest.param("<p>a</p><!--c--><p>b</p>", "<p>a</p><p>b</p>", id="strip-comment"),
        pytest.param("<p>a</p><p>b</p>", "<p>a</p><p>b</p>", id="omit-off-keeps-all-tags"),
        # space, tab, line feed, form feed and carriage return all fold to one space
        pytest.param("a\tb\nc\x0cd\re f", "a b c d e f", id="collapse-all-whitespace-characters"),
        pytest.param("<p>a<br>b</p>", "<p>a<br>b</p>", id="void-element-minified"),
        pytest.param("<div></div>", "<div></div>", id="empty-element-minified"),
        pytest.param("<script>a  <  b</script>", "<script>a  <  b</script>", id="rawtext-script-preserved"),
    ],
)
def test_minify_fragment(source: str, expected: str) -> None:
    assert frag(source, omit_optional_tags=False) == expected


def frag(
    source: str,
    *,
    collapse_whitespace: bool = True,
    omit_optional_tags: bool = True,
    unquote_attributes: bool = True,
    strip_comments: bool = True,
) -> str:
    # Minify source parsed in a <div> fragment context, returning the inner markup.
    #
    # A <div> is never an optional tag and carries no attributes, so it survives every
    # transform unchanged and strips cleanly, leaving just the minified content.
    layout = Minify(
        collapse_whitespace=collapse_whitespace,
        omit_optional_tags=omit_optional_tags,
        unquote_attributes=unquote_attributes,
        strip_comments=strip_comments,
    )
    out = parse_fragment(source, "div").serialize(Html(layout=layout))
    assert out.startswith("<div>")
    assert out.endswith("</div>")
    return out[len("<div>") : -len("</div>")]


def test_collapse_off_keeps_whitespace() -> None:
    assert frag("a   b", collapse_whitespace=False, omit_optional_tags=False) == "a   b"


def test_collapse_minimal_formatter_keeps_nbsp_literal() -> None:
    # MINIMAL folds ASCII whitespace but leaves the non-break space literal
    out = parse_fragment("a   \u00a0   b", "div").serialize(
        Html(layout=Minify(omit_optional_tags=False), formatter=Formatter.MINIMAL)
    )
    assert out == "<div>a \u00a0 b</div>"


def test_collapse_named_formatter() -> None:
    out = parse_fragment("caf\u00e9   &  th\u00e9", "div").serialize(
        Html(layout=Minify(omit_optional_tags=False), formatter=Formatter.NAMED_ENTITIES)
    )
    assert out == "<div>caf&eacute; &amp; th&eacute;</div>"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a=b", '<a x="a=b">', id="equals"),
        pytest.param("a<b", '<a x="a&lt;b">', id="lt"),
        pytest.param("a>b", '<a x="a&gt;b">', id="gt"),
        pytest.param("a`b", '<a x="a`b">', id="backtick"),
        pytest.param("a&b", '<a x="a&amp;b">', id="amp"),
        pytest.param("ab/", '<a x="ab/">', id="trailing-slash"),
    ],
)
def test_unquote_keeps_quotes_on_unsafe(value: str, expected: str) -> None:
    # the value contains a character that bars unquoting, so it stays quoted
    out = frag(f'<a x="{value}">', omit_optional_tags=False)
    assert out.startswith(expected)


def test_unquote_off_keeps_quotes() -> None:
    assert frag("<a href='x'>t</a>", unquote_attributes=False, omit_optional_tags=False) == '<a href="x">t</a>'


def test_minify_sorted_bare_name_before_equals_name() -> None:
    out = parse_fragment("<p =b='' -a=''>x</p>", "div").serialize(
        Html(layout=Minify(omit_optional_tags=False), sort_attributes=True)
    )
    assert out == '<div><p -a="" =b>x</p></div>'


def test_keep_comment_when_off() -> None:
    assert frag("<!--c-->x", strip_comments=False, omit_optional_tags=False) == "<!--c-->x"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<ul><li>a</li><li>b</li></ul>", "<ul><li>a<li>b</ul>", id="li-followed"),
        pytest.param("<ul><li>a</li></ul>", "<ul><li>a</ul>", id="li-last"),
        pytest.param("<ul><li>a</li>text</ul>", "<ul><li>a</li>text</ul>", id="li-before-text-kept"),
        pytest.param("<dl><dt>a</dt><dt>b</dt></dl>", "<dl><dt>a<dt>b</dt></dl>", id="dt-before-dt"),
        pytest.param("<dl><dt>a</dt><dd>b</dd></dl>", "<dl><dt>a<dd>b</dl>", id="dt-before-dd"),
        pytest.param("<dl><dd>a</dd><dd>b</dd></dl>", "<dl><dd>a<dd>b</dl>", id="dd-before-dd"),
        pytest.param("<dl><dd>a</dd><dt>b</dt></dl>", "<dl><dd>a<dt>b</dt></dl>", id="dd-before-dt"),
        pytest.param("<dl><dd>a</dd></dl>", "<dl><dd>a</dl>", id="dd-last"),
        pytest.param("<ruby>a<rt>b</rt><rt>c</rt></ruby>", "<ruby>a<rt>b<rt>c</ruby>", id="rt-before-rt"),
        pytest.param("<ruby>a<rt>b</rt><rp>c</rp></ruby>", "<ruby>a<rt>b<rp>c</ruby>", id="rt-before-rp"),
        pytest.param("<ruby>a<rt>b</rt></ruby>", "<ruby>a<rt>b</ruby>", id="rt-last"),
        pytest.param("<rt>a</rt><rt>b</rt>", "<rt>a</rt><rt>b", id="rt-before-rt-outside-ruby-kept"),
        pytest.param("<rt>a</rt><rp>b</rp>", "<rt>a</rt><rp>b", id="rt-before-rp-outside-ruby-kept"),
        pytest.param(
            "<select><optgroup><option>a</option></optgroup><optgroup><option>b</option></optgroup></select>",
            "<select><optgroup><option>a<optgroup><option>b</select>",
            id="optgroup-before-optgroup",
        ),
        pytest.param(
            "<select><optgroup><option>a</option></optgroup></select>",
            "<select><optgroup><option>a</select>",
            id="optgroup-last",
        ),
        pytest.param(
            "<optgroup>a</optgroup><optgroup>b</optgroup>",
            "<optgroup>a</optgroup><optgroup>b",
            id="optgroup-before-optgroup-outside-select-kept",
        ),
        pytest.param(
            "<select><option>a</option><option>b</option></select>",
            "<select><option>a<option>b</select>",
            id="option-before-option",
        ),
        pytest.param(
            "<select><option>a</option><optgroup><option>b</option></optgroup></select>",
            "<select><option>a<optgroup><option>b</select>",
            id="option-before-optgroup",
        ),
        pytest.param(
            "<table><thead><tr><th>h</th></tr></thead><tbody><tr><td>a</td></tr></tbody></table>",
            "<table><thead><tr><th>h<tbody><tr><td>a</table>",
            id="thead-before-tbody",
        ),
        pytest.param(
            "<table><thead><tr><th>h</th></tr></thead><tfoot><tr><td>f</td></tr></tfoot></table>",
            "<table><thead><tr><th>h<tfoot><tr><td>f</table>",
            id="thead-before-tfoot",
        ),
        pytest.param(
            "<table><tbody><tr><td>a</td></tr></tbody><tbody><tr><td>b</td></tr></tbody></table>",
            "<table><tbody><tr><td>a<tbody><tr><td>b</table>",
            id="tbody-before-tbody",
        ),
        pytest.param(
            "<table><tfoot><tr><td>f</td></tr></tfoot></table>",
            "<table><tfoot><tr><td>f</table>",
            id="tfoot-last",
        ),
        pytest.param(
            "<table><tr><td>a</td></tr><tr><td>b</td></tr></table>",
            "<table><tbody><tr><td>a<tr><td>b</table>",
            id="tr-before-tr",
        ),
        pytest.param(
            "<table><tr><td>a</td><th>b</th></tr></table>",
            "<table><tbody><tr><td>a<th>b</table>",
            id="td-before-th",
        ),
        pytest.param("<p>a</p><p>b</p>", "<p>a<p>b", id="p-before-p"),
        pytest.param("<p>a</p><ul><li>x</li></ul>", "<p>a<ul><li>x</ul>", id="p-before-ul"),
        pytest.param("<ol><li><p>a</p></li></ol>", "<ol><li><p>a</ol>", id="p-last-in-li"),
        pytest.param("<a><p>a</p></a>", "<a><p>a</p></a>", id="p-last-in-a-kept"),
        pytest.param("<p>a</p><svg></svg>", "<p>a</p><svg></svg>", id="p-before-foreign-kept"),
    ],
)
def test_omit_end_tags(source: str, expected: str) -> None:
    assert frag(source, strip_comments=False) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<rt></rt><rp>x</rp>", "<rt></rt><rp>x", id="rt-before-rp-in-body"),
        pytest.param(
            "<optgroup></optgroup><optgroup>x</optgroup>", "<optgroup></optgroup><optgroup>x", id="optgroup-in-body"
        ),
        pytest.param(
            "<ruby><svg><foreignObject><rt></rt><rp>x</rp></foreignObject></svg></ruby>",
            "<ruby><svg><foreignObject><rt></rt><rp>x</rp></foreignObject></svg></ruby>",
            id="rt-across-integration-point",
        ),
        pytest.param(
            "<template><rt></rt><rt>b</rt></template>",
            "<template><rt></rt><rt>b</template>",
            id="rt-in-template-content",
        ),
    ],
)
def test_omit_keeps_end_tag_outside_required_ancestor(source: str, expected: str) -> None:
    # rt/rp imply-close only inside a ruby, optgroup only inside a select; outside that
    # scope -- directly in <body>, split from the ruby by an SVG integration point, or as
    # a direct child of a template's content fragment -- the following start tag reparents
    # into the element, so the end tag must survive.
    once = parse(source).serialize(Html(layout=Minify()))
    assert once == expected
    assert parse(once).html == parse(source).html
    assert parse(once).serialize(Html(layout=Minify())) == once


_PHRASING_PARENTS: Final[list[str]] = ["span", "label", "q", "sup", "ruby", "option", "slot", "picture", "my-el", "del"]


@pytest.mark.parametrize("child", ["p", "li", "dd"])
@pytest.mark.parametrize("parent", _PHRASING_PARENTS)
def test_omit_keeps_end_tag_last_in_phrasing_parent(parent: str, child: str) -> None:
    # `</span>` reaches the special p/li/dd on the open stack and is ignored, so an
    # omitted end tag would pull the parent's next sibling into the child
    source: Final = f"<!DOCTYPE html><div><{parent}><{child}>x</{child}></{parent}>y</div>"
    assert minify(source) == f"<!DOCTYPE html><div><{parent}><{child}>x</{child}></{parent}>y</div>"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<button><p>x</p></button>y", "<button><p>x</button>y", id="p-in-button"),
        pytest.param("<form><p>x</p></form>y", "<form><p>x</form>y", id="p-in-form"),
        pytest.param("<h1><p>x</p></h1>y", "<h1><p>x</h1>y", id="p-in-heading"),
        pytest.param("<dl><dt><p>x</p></dt></dl>", "<dl><dt><p>x</dt></dl>", id="p-in-dt"),
        pytest.param("<object><p>x</p></object>y", "<object><p>x</object>y", id="p-in-object"),
        pytest.param("<table><tr><td><p>x</p></td></tr></table>", "<table><tbody><tr><td><p>x</table>", id="p-in-cell"),
        pytest.param("<template><p>x</p></template>y", "<template><p>x</template>y", id="p-in-template"),
        pytest.param(
            "<svg><foreignObject><p>x</p></foreignObject></svg>y",
            "<svg><foreignObject><p>x</p></foreignObject></svg>y",
            id="p-in-foreign-kept",
        ),
    ],
)
def test_omit_last_child_end_tag_by_parent(source: str, expected: str) -> None:
    assert frag(source, strip_comments=False) == expected


_INTEGRATION_POINTS: Final[list[tuple[str, str]]] = [
    ("svg", "foreignObject"),
    ("svg", "desc"),
    ("svg", "title"),
    ("math", "mi"),
    ("math", "mo"),
    ("math", "mn"),
    ("math", "ms"),
    ("math", "mtext"),
    ("math", "annotation-xml encoding=text/html"),
]


@pytest.mark.parametrize("child", ["rt", "rp", "optgroup", "option"])
@pytest.mark.parametrize(("root", "point"), _INTEGRATION_POINTS)
def test_omit_keeps_end_tag_last_in_integration_point(root: str, point: str, child: str) -> None:
    # `</foreignObject>` under an open html <option> matches no html element, stops at the special foreignObject and
    # is ignored, so an omitted end tag would turn the foreign siblings after it into html elements inside the child
    name: Final = point.partition(" ")[0]
    source: Final = f"<{root}><{point}><{child}>a</{child}></{name}><textarea>b</textarea></{root}>c"
    assert frag(source) == source
    assert parse_fragment(frag(source), "div").html == parse_fragment(source, "div").html


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<rp><rp>a</rp></rp>b", "<rp><rp>a</rp></rp>b", id="rp-in-rp"),
        pytest.param("<rt><rt>a</rt></rt>b", "<rt><rt>a</rt></rt>b", id="rt-in-rt"),
        pytest.param(
            "<optgroup><optgroup>a</optgroup></optgroup>b",
            "<optgroup><optgroup>a</optgroup></optgroup>b",
            id="optgroup-in-optgroup",
        ),
        pytest.param(
            "<rt><optgroup><rt>a</rt></optgroup></rt>b",
            "<rt><optgroup><rt>a</rt></optgroup></rt>b",
            id="rt-in-optgroup-in-rt",
        ),
        pytest.param(
            "<option><rt>a</rt></option><option>b</option>c",
            "<option><rt>a</rt><option>b</option>c",
            id="rt-in-option-before-option",
        ),
        pytest.param(
            "<span><optgroup><option>a</option></optgroup></span>b",
            "<span><optgroup><option>a</span>b",
            id="option-in-optgroup-omitted",
        ),
        pytest.param("<span><rt>a</rt></span>b", "<span><rt>a</span>b", id="rt-in-span-omitted"),
    ],
)
def test_omit_last_child_end_tag_in_rt_rp_optgroup_option(source: str, expected: str) -> None:
    # an rt/rp/optgroup/option parent either loses its own end tag to a same-named child or, with its end tag omitted
    # too, is not closed by the start tag that follows; an <option> ending its <optgroup> has neither problem, and
    # any other html parent's end tag walks past the non-special child, so those end tags still drop
    once: Final = frag(source)
    assert once == expected
    assert parse_fragment(once, "div").html == parse_fragment(source, "div").html


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "<span><li><dd>a</dd></li><li>b</li></span>c",
            "<span><li><dd>a</dd><li>b</li></span>c",
            id="dd-ends-li-before-li",
        ),
        pytest.param(
            "<span><dd><li>a</li></dd><dd>b</dd></span>c",
            "<span><dd><li>a</li><dd>b</dd></span>c",
            id="li-ends-dd-before-dd",
        ),
        pytest.param(
            "<span><dt><li>a</li></dt><dd>b</dd></span>c",
            "<span><dt><li>a</li><dd>b</dd></span>c",
            id="li-ends-dt-before-dd",
        ),
        pytest.param(
            "<span><dd><li><dd>a</dd></li></dd></span>c",
            "<span><dd><li><dd>a</dd></li></dd></span>c",
            id="dd-in-li-in-dd",
        ),
        pytest.param(
            "<svg><foreignObject><li><dd>a</dd></li><li>b</li></foreignObject><desc>c</desc></svg>",
            "<svg><foreignObject><li><dd>a</dd><li>b</li></foreignObject><desc>c</desc></svg>",
            id="dd-ends-li-in-integration-point",
        ),
        pytest.param("<ul><li><p>a</p></li><li>b</li></ul>", "<ul><li><p>a<li>b</ul>", id="p-ends-li-omitted"),
    ],
)
def test_omit_keeps_item_end_tag_last_in_item(source: str, expected: str) -> None:
    # an li/dd/dt parent's own end tag goes before the next item, whose start tag stops at the open li or dd
    assert frag(source) == expected
    assert parse_fragment(expected, "div").html == parse_fragment(source, "div").html


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "<span><li><dd>a</dd></li><li>b</li></span>c", "<span><li><dd>a</dd><li>b</li></span>c", id="dd-in-li"
        ),
        pytest.param(
            "<span><dd><li>a</li></dd><dd>b</dd></span>c", "<span><dd><li>a</li><dd>b</dd></span>c", id="li-in-dd"
        ),
        pytest.param(
            "<span><dt><li>a</li></dt><dd>b</dd></span>c", "<span><dt><li>a</li><dd>b</dd></span>c", id="li-in-dt"
        ),
        pytest.param("<template><li>a</li></template>b", "<template><li>a</template>b", id="li-in-template-dropped"),
        pytest.param("<rt><rt>a</rt></rt>b", "<rt><rt>a</rt></rt>b", id="rt-in-rt"),
        pytest.param(
            "<optgroup><optgroup>a</optgroup></optgroup>b",
            "<optgroup><optgroup>a</optgroup></optgroup>b",
            id="optgroup-in-optgroup",
        ),
        pytest.param("<rt><rp>a</rp></rt>b", "<rt><rp>a</rp></rt>b", id="rp-in-rt"),
        pytest.param(
            "<rp><option><rp>a</rp></option></rp>b", "<rp><option><rp>a</rp></option></rp>b", id="option-in-rp"
        ),
        pytest.param(
            "<option><rt>a</rt></option><option>b</option>c", "<option><rt>a</rt><option>b</option>c", id="rt-in-option"
        ),
        pytest.param(
            "<math><mi><option>a</option></mi></math>b", "<math><mi><option>a</option></mi></math>b", id="option-in-mi"
        ),
        pytest.param(
            "<select><optgroup><option>a</option></optgroup></select>b",
            "<select><optgroup><option>a</select>b",
            id="option-in-optgroup-dropped",
        ),
    ],
)
def test_omit_keeps_last_child_end_tag_the_parent_does_not_close(source: str, expected: str) -> None:
    # an li or dd holds the next item's start tag inside it, an end tag of the child's own name closes only the
    # child, an rt, rp or option may lose its own end tag too, and a foreign parent's end tag is ignored while an
    # HTML child is open: in each case what follows would reparse inside the child
    assert frag(source) == expected
    assert parse(f"<div>{expected}</div>").html == parse(f"<div>{source}</div>").html


def test_omit_keeps_p_end_inside_formatting() -> None:
    # the <p> ends inside a reconstructed <i>, so dropping </p> would change the reparse
    out = frag("<i>a<p>b</i>", strip_comments=False)
    assert "</p>" in out


def test_omit_keeps_p_end_before_formatting_sibling() -> None:
    out = frag("<div><p>a</p><b>x</b></div>", strip_comments=False)
    assert "</p>" in out


# the HTML parser's open-element limit (TH_MAX_TREE_DEPTH); deeper trees come only from adoption or the DOM
_PARSER_DEPTH: Final[int] = 512


def test_omit_end_tag_guard_stays_linear_in_nesting(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: _adopted_serialize("<th>" * n + "</th>" * n), (500, 2000))


def _adopted_serialize(xml: str) -> partial[str]:
    host: Final = parse_fragment("", "div")
    root: Final = parse_xml(xml).root
    assert isinstance(root, Element)
    host.append(root)
    return partial(host.serialize, Html(layout=Minify()), inner=True)


def test_omit_keeps_end_tags_over_trailing_formatting_past_parser_depth() -> None:
    # the <i> stays on every cell's trailing path however deep it lies, and a following sibling takes it off
    depth: Final = _PARSER_DEPTH + 37
    opened, closed = "<th>" * depth, "</th>" * depth
    assert _adopted(f"{opened}<i>x</i>{closed}") == f"{opened}<i>x</i>{closed}"
    assert _adopted(f"{opened}<i>x</i>y{closed}") == f"{opened}<i>x</i>y"


def _adopted(xml: str) -> str:
    return _adopted_serialize(xml)()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<ul><li><xmp><b/></xmp></li></ul>", "<ul><li><xmp><b></b></xmp></li></ul>", id="raw-text"),
        pytest.param("<ul><li><br><b/></br></li></ul>", "<ul><li><br></li></ul>", id="void"),
        pytest.param("<ul><li><xmp><b/>x</xmp></li></ul>", "<ul><li><xmp><b></b>x</xmp></ul>", id="not-trailing"),
        pytest.param(
            "<ul><li><xmp><span><b/></span></xmp></li></ul>",
            "<ul><li><xmp><span><b></b></span></xmp></li></ul>",
            id="raw-text-under-element",
        ),
    ],
)
def test_omit_end_tag_over_child_the_walk_does_not_descend_into(source: str, expected: str) -> None:
    # a raw-text or void element holds an element only in a tree no parser built; the trailing path runs through it
    assert _adopted(source) == expected


@pytest.mark.parametrize(
    ("depth", "expected"),
    [
        pytest.param(_PARSER_DEPTH - 9, "<rp><rp></span>", id="within-parser-depth"),
        pytest.param(_PARSER_DEPTH + 37, "<rp></rp><rp></span>", id="past-parser-depth"),
    ],
)
def test_omit_scope_climb_stops_at_parser_depth(depth: int, expected: str) -> None:
    # past the depth a reparse can reach the ruby is out of reach, so the end tag stays
    out: Final = _adopted(f"<ruby>{'<span>' * depth}<rp/><rp/>{'</span>' * depth}</ruby>")
    assert out.startswith(f"<ruby>{'<span>' * depth}{expected}")


def test_omit_scope_climb_stays_linear_over_sibling_run(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(
        lambda n: _adopted_serialize(f"<ruby>{'<span>' * n}{'<rp/>' * n}{'</span>' * n}</ruby>"), (500, 2000)
    )


def test_omit_li_end_dropped_with_attributed_child() -> None:
    # the <li> ends in a non-formatting <span>, so its end tag drops; the child's
    # unquoted attribute is unaffected
    assert frag("<ul><li><span id='x'>t</span></li></ul>", strip_comments=False) == "<ul><li><span id=x>t</span></ul>"


def test_omit_start_tags_document() -> None:
    assert parse("<html><head><title>x</title></head><body><p>a</p></body></html>").serialize(
        Html(layout=Minify())
    ) == ("<title>x</title><p>a")


def test_omit_html_start_kept_when_first_is_comment() -> None:
    out = parse("<html><!--c--><title>x</title>").serialize(Html(layout=Minify(strip_comments=False)))
    assert out.startswith("<html><!--c-->")


def test_omit_start_kept_with_attributes() -> None:
    out = parse("<html lang='en'><body>x</body></html>").serialize(Html(layout=Minify()))
    assert out.startswith("<html lang=en>")


def test_omit_body_start_kept_before_whitespace() -> None:
    out = parse("<body> x</body>").serialize(Html(layout=Minify()))
    assert out.startswith("<body> x")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # a value holding a double quote can't be unquoted; WHATWG escapes it to &quot;
        pytest.param("<span x='a\"b'>t</span>", '<span x="a&quot;b">t</span>', id="double-quote"),
        # a value holding a single quote can't be unquoted; WHATWG leaves it literal
        pytest.param('<span x="a\'b">t</span>', '<span x="a\'b">t</span>', id="single-quote"),
    ],
)
def test_unquote_keeps_quotes_on_quote_chars(source: str, expected: str) -> None:
    assert frag(source, omit_optional_tags=False) == expected


def test_html_body_end_kept_before_comment() -> None:
    # the comment after </body> keeps the body end tag; </html> still drops (nothing follows it)
    out = parse("<html><body>x</body><!--tail--></html>").serialize(Html(layout=Minify(strip_comments=False)))
    assert out.endswith("</body><!--tail-->")


def test_head_end_kept_before_whitespace() -> None:
    out = parse("<html><head><title>t</title></head> <body>x</body></html>").serialize(Html(layout=Minify()))
    assert "</head>" in out


def test_head_start_omitted_when_empty() -> None:
    assert parse("<html><head></head><body>x</body></html>").serialize(Html(layout=Minify())) == "x"


def test_body_start_kept_before_meta() -> None:
    out = parse("<body><meta charset='utf-8'>x</body>").serialize(Html(layout=Minify()))
    assert out.startswith("<body><meta")


def test_omit_in_template_content() -> None:
    # items hang off the template content node; dropping the trailing end tag still
    # reparses identically, since the template close reconstructs the element
    out = parse("<template><li>a</li><li>b</li></template>").serialize(Html(layout=Minify()))
    assert out == "<template><li>a<li>b</template>"


def test_collapse_carriage_return_in_constructed_text() -> None:
    # a parsed document never carries a CR (the tokenizer folds it to LF), but a
    # programmatically built text node can, and it folds like any other whitespace
    assert Text("a\rb\rc").serialize(Html(layout=Minify())) == "a b c"


def test_body_start_omitted_with_empty_first_text() -> None:
    # an empty text node (only buildable programmatically) is neither whitespace nor a
    # comment, so the body start tag is still omitted
    html = Element("html")
    body = Element("body")
    body.append(Text(""))
    body.append(Element("p"))
    html.append(body)
    out = html.serialize(Html(layout=Minify()))
    assert out == "<html><p></html>"


@pytest.mark.parametrize(
    ("tag", "source", "layout", "expected"),
    [
        pytest.param("script", "var a = 1;", Minify(minify_js=JSMinify()), "var a=1", id="script"),
        pytest.param("style", "a { color: red }", Minify(minify_css=CSSMinify()), "a{color:red}", id="style"),
    ],
)
def test_minify_raw_text_joins_an_empty_text_child(tag: str, source: str, layout: Minify, expected: str) -> None:
    element: Final = Element(tag, children=[Text(""), Text(source)])
    assert element.serialize(Html(layout=layout)) == f"<{tag}>{expected}</{tag}>"


def test_omit_dd_end_kept_before_text() -> None:
    assert frag("<dl><dd>a</dd>x</dl>", strip_comments=False) == "<dl><dd>a</dd>x</dl>"


def test_body_start_kept_before_comment() -> None:
    assert parse("<body><!--c-->x</body>").serialize(Html(layout=Minify(strip_comments=False))) == "<body><!--c-->x"


@pytest.mark.parametrize(
    "tag",
    [
        pytest.param(t, id=t)
        for t in (
            "base",
            "basefont",
            "bgsound",
            "link",
            "meta",
            "noframes",
            "noscript",
            "script",
            "style",
            "template",
            "title",
        )
    ],
)
def test_body_start_kept_before_head_element(tag: str) -> None:
    # without the body start tag the "after head" insertion mode files the element under <head>
    source: Final = f"<body><{tag}></{tag}>x</body>"
    once: Final = parse(source).serialize(Html(layout=Minify()))
    assert once.startswith(f"<body><{tag}")
    assert parse(once).serialize(Html()) == parse(source).serialize(Html())


def test_body_start_omitted_before_foreign_element() -> None:
    assert (
        parse("<body><svg><title>t</title></svg>x").serialize(Html(layout=Minify())) == "<svg><title>t</title></svg>x"
    )


def test_empty_element_as_root_keeps_end_tag() -> None:
    para = parse_fragment("<p></p>", "div").find("p")
    assert para is not None
    assert para.serialize(Html(layout=Minify())) == "<p></p>"


def test_rawtext_element_as_root() -> None:
    script = parse_fragment("<script>a<b</script>", "div").find("script")
    assert script is not None
    assert script.serialize(Html(layout=Minify())) == "<script>a<b</script>"


def test_body_end_omitted_before_noncomment_sibling() -> None:
    html = Element("html")
    body = Element("body")
    body.append(Text("x"))
    html.append(body)
    html.append(Element("div"))
    assert html.serialize(Html(layout=Minify(strip_comments=False))) == "<html>x<div></div></html>"


def test_empty_html_start_omitted() -> None:
    wrap = Element("div")
    wrap.append(Element("html"))
    assert wrap.serialize(Html(layout=Minify())) == "<div></div>"


def test_head_start_kept_before_nonelement() -> None:
    html = Element("html")
    head = Element("head")
    head.append(Text("t"))
    html.append(head)
    html.append(Element("body"))
    assert html.serialize(Html(layout=Minify())) == "<html><head>t</html>"


def test_head_end_omitted_when_last() -> None:
    html = Element("html")
    html.append(Element("head"))
    assert html.serialize(Html(layout=Minify())) == "<html></html>"


def test_head_end_kept_before_comment() -> None:
    out = parse("<html><head></head><!--c--><body>x</body></html>").serialize(Html(layout=Minify(strip_comments=False)))
    assert out.startswith("</head><!--c-->")


def test_omit_kept_for_p_in_foreign_parent() -> None:
    # a <p> built under a foreign (MathML) element keeps its end tag: the parent is not
    # an HTML element, so the "no more content" omission does not apply
    math = parse("<math></math>").find("math")
    assert math is not None
    para = Element("p")
    para.append(Text("x"))
    math.append(para)
    assert math.serialize(Html(layout=Minify())).endswith("<p>x</p></math>")


def test_foreign_end_tags_kept() -> None:
    out = frag("<svg><g><rect></rect></g></svg>", strip_comments=False)
    assert "</g>" in out
    assert "</svg>" in out


_CSS: Final[CSSMinify] = CSSMinify()


def test_minify_css_defaults_off() -> None:
    assert Minify().minify_css is None


def test_minify_css_getter_round_trips() -> None:
    assert Minify(minify_css=CSSMinify()).minify_css == CSSMinify()
    assert Minify(minify_css=CSSMinify(baseline=2021)).minify_css == CSSMinify(baseline=2021)
    assert Minify(minify_css=None).minify_css is None


def test_minify_css_rejects_non_config() -> None:
    with pytest.raises(TypeError, match="minify_css must be a CSSMinify or None"):
        Minify(minify_css=True)  # ty: ignore[invalid-argument-type]  # a bool is no longer accepted


def test_minify_css_equality_and_hash() -> None:
    assert Minify(minify_css=CSSMinify()) != Minify()
    assert Minify(minify_css=CSSMinify()) == Minify(minify_css=CSSMinify())
    assert Minify(minify_css=CSSMinify(baseline=2021)) != Minify(minify_css=CSSMinify())
    assert hash(Minify(minify_css=CSSMinify())) != hash(Minify())
    assert hash(Minify(minify_css=CSSMinify(baseline=2021))) != hash(Minify(minify_css=CSSMinify()))


@pytest.mark.parametrize(
    ("minify_css", "text"),
    [
        pytest.param(None, "minify_css=None", id="off"),
        pytest.param(CSSMinify(), "minify_css=CSSMinify(baseline=None)", id="on"),
        pytest.param(CSSMinify(baseline=2021), "minify_css=CSSMinify(baseline=2021)", id="baseline"),
    ],
)
def test_minify_css_repr(minify_css: CSSMinify | None, text: str) -> None:
    assert repr(Minify(minify_css=minify_css)).endswith(f", {text})")


def test_minify_css_baseline_bounds_output_syntax() -> None:
    # the inset shorthand reached Baseline 2021; below that year the four physical properties stay separate
    source = "<style>a{top:1px;right:2px;bottom:3px;left:4px}</style>"
    assert css_frag(source) == "<style>a{top:1px;right:2px;bottom:3px;left:4px}</style>"
    assert css_frag(source, minify_css=CSSMinify(baseline=2021)) == "<style>a{inset:1px 2px 3px 4px}</style>"


def css_frag(source: str, *, minify_css: CSSMinify | None = _CSS, unquote_attributes: bool = True) -> str:
    # Minify source in a <div> fragment with the CSS pass, returning the inner markup.
    layout = Minify(minify_css=minify_css, unquote_attributes=unquote_attributes)
    out = parse_fragment(source, "div").serialize(Html(layout=layout))
    assert out.startswith("<div>")
    assert out.endswith("</div>")
    return out[len("<div>") : -len("</div>")]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "<style>  a  {  color : red ;  margin : 0 0 0 0 }  </style>",
            "<style>a{color:red;margin:0}</style>",
            id="style-body",
        ),
        pytest.param(
            "<style>@media screen { .a { color: #ff0000 } }</style>",
            "<style>@media screen{.a{color:red}}</style>",
            id="style-body-at-rule",
        ),
        pytest.param(
            "<style>.a{color:blue}.b{c:d\\(;color:red}.a{color:green}</style>",
            "<style>.a{color:blue}.b{c:d\\(;color:red}.a{color:green}</style>",
            id="style-body-keeps-rule-order-past-property",
        ),
        pytest.param("<style></style>", "<style></style>", id="empty-style-element"),
        pytest.param("<style>   </style>", "<style></style>", id="whitespace-only-style-folds-to-empty"),
        pytest.param(
            '<style>a::before { content: "café€\U0001f600" }</style>',
            '<style>a:before{content:"café€\U0001f600"}</style>',
            id="style-body-transcodes-non-ascii",
        ),
        # a <script> is raw text too but never CSS, so the style pass leaves it verbatim
        pytest.param("<script>a  <  b</script>", "<script>a  <  b</script>", id="script-untouched"),
        pytest.param(
            '<p style="color: red ; margin : 0 0 0 0">x</p>', "<p style=color:red;margin:0>x", id="style-attr"
        ),
        # a style attribute holds no nested rule, so the text after a {} block stays in the declaration the block voids
        pytest.param('<p style="margin:0{}top:0;left:0">x</p>', "<p style=margin:0{};left:0>x", id="style-attr-block"),
        # a minified value carrying a double quote (a string literal) keeps its quotes and escapes
        pytest.param(
            "<p style='content: \"hi there\"'>x</p>",
            '<p style="content:&quot;hi there&quot;">x',
            id="attr-needs-quotes",
        ),
        pytest.param('<p style="  /* only a comment */  ">x</p>', "<p style>x", id="empty-attr-folds-to-bare-name"),
        # the style attribute is CSS on any element, so an SVG rect's declarations minify too
        pytest.param(
            '<svg><rect style="fill: #ffffff"/></svg>', "<svg><rect style=fill:#fff></rect></svg>", id="foreign-attr"
        ),
        # the minified body keeps `<` apart from `/`, so the parser still ends <style> where the source did
        pytest.param(
            "<style>a{b:</**//style><b>x</b>}</style>",
            "<style>a{b:< /style><b>x< /b>}</style>",
            id="style-body-never-spells-end-tag",
        ),
        pytest.param(
            '<style>a::before{content:"</\\\nstyle><b>x</b>"}</style>',
            '<style>a::before{content:"</\\\nstyle><b>x</b>"}</style>',
            id="style-body-kept-when-minifying-would-spell-end-tag",
        ),
        # a longer name after the end tag closes no element, and does not excuse a minified form that spells one
        pytest.param(
            '<style>a{c:"</stylex";b:"<\\\n/style><b>x</b>"}</style>',
            '<style>a{c:"</stylex";b:"<\\\n/style><b>x</b>"}</style>',
            id="style-body-kept-when-source-spells-end-tag-prefix",
        ),
        pytest.param(
            "<style>a{--x:</\\73tyle><b>x</b>}</style>",
            "<style>a{--x:</\\73tyle><b>x</b>}</style>",
            id="style-body-kept-when-an-escape-would-spell-end-tag",
        ),
    ],
)
def test_minify_css_output(source: str, expected: str) -> None:
    assert css_frag(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<style>  a  {  color : red  }  </style>", "<style>  a  {  color : red  }  </style>", id="body"),
        pytest.param('<p style="color: red">x</p>', '<p style="color: red">x', id="attr"),
    ],
)
def test_minify_css_off_is_noop(source: str, expected: str) -> None:
    assert css_frag(source, minify_css=None) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('<p style="color: red">x</p>', '<p style="color:red">x', id="minified-value-stays-quoted"),
        pytest.param('<p style="  ">x</p>', '<p style="">x', id="empty-value-stays-quoted"),
    ],
)
def test_minify_css_with_unquote_off(source: str, expected: str) -> None:
    assert css_frag(source, unquote_attributes=False) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('<a href="/some/path">x</a>', "<a href=/some/path>x</a>", id="href-4-char-name"),
        pytest.param('<a class="a b c">x</a>', '<a class="a b c">x</a>', id="class-5-char-name"),
    ],
)
def test_non_style_attribute_untouched_by_minify_css(source: str, expected: str) -> None:
    # only the style attribute is CSS; a same-length (class) or different-length (href) name is left as-is
    assert css_frag(source) == expected


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        pytest.param("ascii", '<p style="content:&quot;ascii&quot;">x', id="one-byte"),
        pytest.param("café", '<p style="content:&quot;café&quot;">x', id="two-byte"),
        pytest.param("a€b", '<p style="content:&quot;a€b&quot;">x', id="three-byte"),
        pytest.param("a\U0001f600b", '<p style="content:&quot;a\U0001f600b&quot;">x', id="four-byte"),
    ],
)
def test_style_attribute_transcodes_every_utf8_length(literal: str, expected: str) -> None:
    # each UTF-8 length exercises one arm of the code-point-to-UTF-8 transcoder the CSS engine reads
    assert css_frag(f"<p style='content: \"{literal}\"'>x</p>") == expected


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("<style>  a  {  color : #ff0000 }  </style>", id="style-body"),
        pytest.param('<p style="color: red ; margin: 0 0 0 0">x</p>', id="style-attr"),
        pytest.param("<p style='content: \"café\"'>x</p>", id="style-attr-unicode"),
        pytest.param('<svg><rect style="fill: #ffffff"/></svg>', id="foreign-style-attr"),
    ],
)
def test_minify_css_is_idempotent_and_reparse_safe(source: str) -> None:
    layout = Minify(minify_css=CSSMinify())
    once = parse(source).serialize(Html(layout=layout))
    assert parse(once).serialize(Html(layout=layout)) == once


@pytest.mark.parametrize(
    "layout",
    [pytest.param(Minify(), id="default"), pytest.param(Minify(minify_css=CSSMinify()), id="minify-css")],
)
def test_minify_idempotent_over_tree_construction(wpt_html_tree_corpus: WptHtmlTreeCorpus, layout: Minify) -> None:
    # only the subset the plain serializer round-trips can be asked of the minifier;
    # the rest are inherently non-idempotent adoption-agency reconstructions
    failures = [
        f"{case['file']}: {data!r}\n  once:    {parse(data).serialize(Html(layout=layout))!r}\n"
        f"  reparse: {parse(parse(data).serialize(Html(layout=layout))).serialize(Html(layout=layout))!r}"
        for case in wpt_html_tree_corpus["cases"]
        if case["context"] is None and case["scripting"] is not True
        for data in [case["data"]]
        if _plain_roundtrips(data) and not _minify_idempotent(data, layout)
    ]
    assert not failures, f"{len(failures)} non-idempotent\n\n" + "\n\n".join(failures[:5])


def _plain_roundtrips(source: str) -> bool:
    once = parse(source).serialize()
    return once == parse(once).serialize()


def _minify_idempotent(source: str, layout: Minify) -> bool:
    once = parse(source).serialize(Html(layout=layout))
    return once == parse(once).serialize(Html(layout=layout))


def test_minify_idempotent_over_large_document() -> None:
    # a large well-formed document exercises every transform at scale (whitespace,
    # optional tags, attribute unquoting, comment stripping) past the serialization
    # buffer's growth, where the per-snippet suite stays small
    section = (
        "<section id='s{i}'>\n"
        "  <h2>Heading {i} &amp; more</h2>\n"
        "  <p class='lead'>Some   prose with    spaces and a <a href='/x{i}'>link</a> here.</p>\n"
        "  <ul>\n    <li>one</li>\n    <li>two</li>\n  </ul>\n"
        "  <!-- note {i} -->\n"
        "  <table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>\n"
        "</section>\n"
    )
    big = (
        "<!doctype html><html><head><title>Big</title></head><body>\n"
        f"{''.join(section.format(i=index) for index in range(500))}</body></html>"
    )
    layout = Minify()
    once = parse(big).serialize(Html(layout=layout))
    assert once == parse(once).serialize(Html(layout=layout))
    assert len(once) < len(parse(big).serialize())  # minification actually shrinks the document


_SCRIPT: Final[str] = "function f(){ var longName = 1 + 2 ; return longName }"


def test_scripts_untouched_without_minify_js() -> None:
    # the default Minify leaves JavaScript exactly as written
    assert script(_SCRIPT) == f"<script>{_SCRIPT}</script>"


def script(source: str, minify_js: JSMinify | None = None) -> str:
    # Serialize source as the single child of a <script> under Minify(minify_js=...),
    # returning just the script element so the assertion is the minified (or verbatim) body.
    out = parse_fragment(f"<script>{source}</script>", "div").serialize(Html(layout=Minify(minify_js=minify_js)))
    assert out.startswith("<div><script>")
    assert out.endswith("</script></div>")
    return out[len("<div>") : -len("</div>")]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(JSMinify(), "function f(){return 3}", id="full"),
        pytest.param(JSMinify(mangle=False), "function f(){var longName=3;return longName}", id="no-mangle"),
        pytest.param(JSMinify(fold=False), "function f(){return 1+2}", id="no-fold"),
        pytest.param(
            JSMinify(mangle=False, fold=False), "function f(){var longName=1+2;return longName}", id="ws-only"
        ),
    ],
)
def test_minify_js_passes_thread_through(options: JSMinify, expected: str) -> None:
    assert script(_SCRIPT, minify_js=options) == f"<script>{expected}</script>"


@pytest.mark.parametrize(
    "script_type",
    [
        pytest.param("", id="empty"),
        pytest.param("text/javascript", id="text-javascript"),
        pytest.param("application/javascript", id="application-javascript"),
        pytest.param("MODULE", id="module-uppercase"),
        pytest.param("application/x-javascript", id="x-javascript-mid-list"),
        pytest.param("text/javascript1.5", id="versioned"),
    ],
)
def test_javascript_types_are_minified(script_type: str) -> None:
    # top-level names are global, so they are kept; the JS pass still runs, visible as the
    # collapsed whitespace (a non-JS type would leave the spaces, see the test below). The
    # empty case pins type="" (an explicit empty type is still a classic script).
    source = f'<script type="{script_type}">var topLevel = 1 + 2</script>'
    out = parse_fragment(source, "div").serialize(Html(layout=Minify(minify_js=JSMinify())))
    assert "var topLevel=3" in out


@pytest.mark.parametrize(
    "script_type",
    [
        pytest.param("application/json", id="json"),
        pytest.param("importmap", id="importmap"),
        pytest.param("text/html", id="template"),
        pytest.param("speculationrules", id="speculationrules"),
    ],
)
def test_non_javascript_types_pass_through(script_type: str) -> None:
    # a non-JS payload that happens to be valid JS (an array literal) must still be left
    # byte-for-byte: minifying JSON as JS could change quoting or numbers and break it
    body = "[1,    2,    3]"
    source = f'<script type="{script_type}">{body}</script>'
    out = parse_fragment(source, "div").serialize(Html(layout=Minify(minify_js=JSMinify())))
    assert body in out


@pytest.mark.parametrize(
    ("script_type", "expected"),
    [
        pytest.param("", "<script type>f(a)</script>", id="classic-comment"),
        pytest.param("text/javascript", "<script type=text/javascript>f(a)</script>", id="javascript-comment"),
        pytest.param("module", "<script type=module>f(a<! --b)</script>", id="module-operators"),
        pytest.param("Module", "<script type=Module>f(a<! --b)</script>", id="module-mixed-case"),
    ],
)
def test_html_like_comment_follows_script_goal(script_type: str, expected: str) -> None:
    # Annex B.1.1 HTML-like comments exist only in the Script goal; a module reads `<!--` as `<`, `!`, `--`
    source = f'<script type="{script_type}">f(a <!--b\n)</script>'
    out = parse_fragment(source, "div").serialize(Html(layout=Minify(minify_js=JSMinify())))
    assert out == f"<div>{expected}</div>"


def test_unparseable_script_emitted_verbatim() -> None:
    # the JS parser cannot handle this; the script falls back to its original bytes rather
    # than breaking the surrounding document
    assert script("function( broken", minify_js=JSMinify()) == "<script>function( broken</script>"


def test_script_function_declaration_sharing_param_name() -> None:
    expected = "<script>function f(a){function a(){}}</script>"
    assert script("function f(param){function param(){}}", minify_js=JSMinify()) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('var a="</scr"+"ipt><img src=x>"', r'var a="\x3c/script><img src=x>"', id="folded-end-tag"),
        pytest.param("x=a < /script>/i", "x=a< /script>/i", id="less-than-before-regex"),
    ],
)
def test_minified_script_spells_no_end_tag(source: str, expected: str) -> None:
    assert script(source, minify_js=JSMinify()) == f"<script>{expected}</script>"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('<!--<script>\nx="</script><img src=x>"\n-->', id="string-in-escaped-section"),
        pytest.param("<!--<script>\nx=String.raw`</SCRIPT>`\n-->", id="tagged-template-in-escaped-section"),
        pytest.param("<!--<script>\n/*! </script> */x()\n-->", id="kept-comment-in-escaped-section"),
        pytest.param('a="<!--";if(0){b="-->"}c="<script>"', id="comment-open-left-unclosed"),
    ],
)
def test_script_that_would_not_reparse_stays_verbatim(source: str) -> None:
    # stripping the `<!--` section or the dead `-->` would let the parser end or extend the element elsewhere
    assert script(source, minify_js=JSMinify()) == f"<script>{source}</script>"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('<script>x="</noscr"+"ipt><img src=x>"</script>', id="folded-string"),
        pytest.param('<script>x="</NoScr"+"Ipt><img src=x>"</script>', id="folded-string-mixed-case"),
        pytest.param('<style>a::before{content:"</noscr\\\nipt><img src=x>"}</style>', id="string-line-continuation"),
    ],
)
def test_minified_content_under_noscript_spells_no_noscript_end_tag(source: str) -> None:
    # a scripting browser reads the whole <noscript> as raw text, so a joined `</noscript` would end it mid-string
    layout = Minify(minify_js=JSMinify(), minify_css=CSSMinify())
    out = parse_fragment(f"<noscript>{source}</noscript>", "div").serialize(Html(layout=layout))
    assert out == f"<div><noscript>{source}</noscript></div>"


def test_content_under_noscript_is_still_minified() -> None:
    # only the end tag closes the element: any other `<`, a `<noscript` start tag included, stays minified
    layout = Minify(minify_js=JSMinify(), minify_css=CSSMinify())
    source = '<script>var  a = "<noscript>" ;</script><style>a {  content : "<noscript>"  }</style>'
    out = parse_fragment(f"<noscript>{source}</noscript>", "div").serialize(Html(layout=layout))
    minified = '<script>var a="<noscript>"</script><style>a{content:"<noscript>"}</style>'
    assert out == f"<div><noscript>{minified}</noscript></div>"


_NOSCRIPT_DOCUMENTS: Final[list[pytest.ParameterSet]] = [
    pytest.param(
        "<!doctype html><head><meta charset=utf-8><noscript><img src=/pixel.png width=1></noscript>"
        "<script src=/app.js></script></head><body id=main><h1>Title</h1>",
        id="head-image",
    ),
    pytest.param(
        "<!doctype html><body><noscript><iframe src=/frame></iframe></noscript><main>x</main>", id="body-first"
    ),
    pytest.param(
        '<p>a<noscript><span title="</noscript><img src=/x.png>">b</span></noscript>c', id="attribute-end-tag"
    ),
    pytest.param("<p>a<noscript><!-- </noscript><em>e</em> --></noscript>z", id="comment-end-tag"),
    pytest.param("<p>a<noscript><!--</noscript/>--></noscript>z", id="slash-after-end-tag"),
    pytest.param("<p>a<noscript><i>b</noscript>c</p><p>d</p>", id="unclosed-formatting"),
    pytest.param("<table><tr><td><noscript><div>a</noscript>b</td></tr></table><p>c</p>", id="table-cell"),
    pytest.param("<p><b>x</p><noscript><a href=/>y</a></noscript>z", id="reconstructed-formatting"),
    pytest.param("<div><noscript><img src=/a.png></noscript></div><frameset><frame src=/b></frameset>", id="frameset"),
    pytest.param("<p>a  b</p><frameset><frame></frameset>", id="frameset-without-noscript"),
    pytest.param("<div><noscript><noscript>a</noscript>b</noscript>c</div><p>d</p>", id="nested"),
    pytest.param("<div><noscript><p>a", id="unclosed-at-end"),
    pytest.param("<select><noscript><option>a</option></noscript></select><noscript><b>b</b></noscript>", id="select"),
    pytest.param("<p>a\r\n<noscript>\r\n<img src=/x.png>\r\n</noscript>\r\nb", id="crlf"),
    pytest.param(
        "<head>\r\n<noscript><img src=/x.png></noscript>\r\n<link rel=a href=b></head><body>c", id="crlf-head"
    ),
    pytest.param("<p>€<noscript>€<img alt=€></noscript><p>€", id="two-byte"),
    pytest.param("<p>\U0001f600<noscript><i>\U0001f600</noscript><p>\U0001f600", id="four-byte"),
    pytest.param("<table><noscript>a</noscript><tr><td>b</td></tr></table>", id="fostered"),
    pytest.param("<template><noscript><p>a</p></noscript></template>", id="template"),
    pytest.param("<table><tr><td><noscript>1</noscript></td></tr><noscript>2</noscript></table>", id="fostered-later"),
    pytest.param("".join(f"<div><noscript><i>{n}</noscript></div><p>{n}</p>" for n in range(10)), id="many-parted"),
    pytest.param(f"{'<div>' * 600}<noscript><img src=/x.png></noscript>", id="past-depth-cap"),
    pytest.param("<p><noscript>a</noscriptx>b</noscript>", id="end-tag-name-continues"),
    pytest.param("<p><noscript>a</nos b</noscript>", id="end-tag-prefix"),
    pytest.param("<head><noscript><style>a { color : red }</style><link rel=x href=y></noscript></head>x", id="style"),
    pytest.param("<b><noscript></b>x</noscript>", id="adoption-agency"),
    pytest.param("<b><b><b><noscript><b>x</noscript>y", id="noahs-ark"),
    pytest.param("<body><noscript><body class=x></noscript>", id="body-attributes"),
    pytest.param("<noscript><html lang=x></noscript>", id="html-attributes"),
    pytest.param("<table><noscript><form></noscript>", id="form-in-table"),
    pytest.param("<svg><noscript/></svg>", id="foreign-self-closing"),
    pytest.param("<svg><noscript>a</noscript></svg><noscript><img src=/a.png></noscript>", id="foreign"),
    pytest.param(
        "<p>a<noscript>  <b>s</b>  </noscript><ul><li><noscript><p>a</noscript></ul>  <p>c</p>", id="stable-first"
    ),
    pytest.param("<ul><li><noscript><p>a</noscript></ul>  <p>b<noscript>  <b>s</b>  </noscript>", id="stable-after"),
    *(
        pytest.param(f"<p>a<noscript><!-- </NoScript{after}--></noscript>b", id=f"comment-end-tag-{name}")
        for name, after in (("space", " "), ("tab", "\t"), ("newline", "\n"), ("form-feed", "\f"), ("cr", "\r"))
    ),
    pytest.param(
        "<div><noscript><!-- </noscript> --></noscript><table><tr><td><noscript><!-- </noscript> --></noscript></td>"
        "</tr><div>x</div></table><noscript><!-- </noscript> --></noscript></div>",
        id="fostered-before-cell",
    ),
    pytest.param(
        "<div><noscript><!-- </noscript> --></noscript><table><tr><td><noscript><!-- </noscript> --></noscript></td>"
        "</tr><div>x</div></table></div>",
        id="fostered-before-last-cell",
    ),
    pytest.param(
        f"{'<div>' * 506}<noscript><!-- </noscript> --></noscript><table><tr><td><noscript>y</noscript></td></tr>"
        "<div>x</div></table>",
        id="fostered-before-cell-past-depth-cap",
    ),
    pytest.param("<div><form></div><p>a  b<noscript><i>c</noscript>d", id="form-pointer-closed"),
    pytest.param("<form><p>a  b<noscript><i>c</noscript>d</form>", id="form-open"),
    pytest.param("<svg><foreignObject><p>a  b<noscript><i>c</noscript>d</foreignObject></svg>", id="foreign-ancestor"),
    pytest.param("<template><p>a  b<noscript><i>c</noscript>d</template>", id="template-ancestor"),
    pytest.param("<table><tr><td>a  b<noscript><i>c</noscript>d</td></tr></table>", id="table-ancestor"),
    pytest.param("<table><colgroup><noscript><i>c</noscript></table>", id="column-group"),
    pytest.param("<head><noscript><noscript><i>c</noscript></head>", id="nested-in-head"),
    pytest.param("<span><noscript><frameset></noscript></span>", id="frameset-in-noscript"),
    pytest.param("<h1><noscript><frameset><x></noscript></h1>", id="frameset-in-noscript-under-heading"),
    pytest.param("<p>a<noscript><frameset></noscript>", id="frameset-ignored-in-noscript"),
]
_NOSCRIPT_LAYOUTS: Final[list[pytest.ParameterSet]] = [
    pytest.param(Minify(), id="default"),
    pytest.param(Minify(strip_comments=False), id="keep-comments"),
    pytest.param(Minify(minify_js=JSMinify(), minify_css=CSSMinify()), id="js-css"),
]


@pytest.mark.parametrize("scripting", [pytest.param(False, id="scripting-off"), pytest.param(True, id="scripting-on")])
@pytest.mark.parametrize("layout", _NOSCRIPT_LAYOUTS)
@pytest.mark.parametrize("source", _NOSCRIPT_DOCUMENTS)
def test_minify_noscript_reparses_to_the_source_tree(source: str, layout: Minify, *, scripting: bool) -> None:
    expected = _reparsed(source, layout, scripting=scripting)
    assert _reparsed(minify(source, layout), layout, scripting=scripting) == expected


def _reparsed(markup: str, layout: Minify, *, scripting: bool) -> str:
    # minifying folds whitespace and drops comments, so the trees compare in the form the layout writes them
    document = parse(markup, scripting=scripting)
    return (strip_comments_node(document) if layout.strip_comments else document).serialize(Html(layout=layout))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "<p>a<noscript>  <img  src=/x.png>  </noscript>  b",
            "<p>a<noscript>  <img  src=/x.png>  </noscript> b",
            id="content-copied",
        ),
        pytest.param(
            "<p>x  y</p><div><noscript><i>a</noscript></div>  <p>b  c</p>",
            "<p>x y<div><noscript><i>a</noscript></div>  <p>b  c</p>",
            id="copied-to-the-end",
        ),
        pytest.param(
            "<!doctype html><head><title>t</title><noscript><img src=/p.gif></noscript><link rel=x href=y></head>",
            "<!DOCTYPE html><title>t</title><noscript><img src=/p.gif></noscript><link rel=x href=y></head>",
            id="copied-from-the-head",
        ),
        pytest.param(
            "<p>x  y<b>z<noscript><i>a</noscript></b>",
            "<p>x  y<b>z<noscript><i>a</noscript></b>",
            id="unchanged-under-formatting",
        ),
    ],
)
def test_minify_copies_noscript_from_the_source(source: str, expected: str) -> None:
    assert minify(source) == expected


def test_empty_script_is_unchanged() -> None:
    assert script("", minify_js=JSMinify()) == "<script></script>"


def test_other_rawtext_elements_are_not_touched() -> None:
    # style is raw text too, but never JavaScript: minify_js must not reach it
    source = "<style>a  {  color : red  }</style>"
    out = parse_fragment(source, "div").serialize(Html(layout=Minify(minify_js=JSMinify())))
    assert "a  {  color : red  }" in out


def test_each_script_in_a_document_is_minified() -> None:
    source = "<script>var aaa = 1</script><script>var bbb = 2</script>"
    out = parse_fragment(source, "div").serialize(Html(layout=Minify(minify_js=JSMinify())))
    assert out == "<div><script>var aaa=1</script><script>var bbb=2</script></div>"


def test_minified_script_is_idempotent() -> None:
    once = script(_SCRIPT, minify_js=JSMinify())
    assert script("function f(){var a=1+2;return a}", minify_js=JSMinify()) == once


def test_minify_js_defaults_off() -> None:
    assert Minify().minify_js is None


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(JSMinify(), id="mangle-fold"),
        pytest.param(JSMinify(mangle=False), id="no-mangle"),
        pytest.param(JSMinify(fold=False), id="no-fold"),
        pytest.param(JSMinify(mangle=False, fold=False), id="neither"),
    ],
)
def test_minify_js_getter_round_trips(config: JSMinify) -> None:
    # rebuild every mangle/fold combination so the getter's two toggle branches both run
    assert Minify(minify_js=config).minify_js == config


def test_minify_js_none_is_explicit_off() -> None:
    assert Minify(minify_js=None).minify_js is None


@pytest.mark.parametrize(
    ("options", "text"),
    [
        pytest.param(None, "minify_js=None", id="off"),
        pytest.param(JSMinify(), "minify_js=JSMinify(mangle=True, fold=True)", id="on"),
        pytest.param(JSMinify(fold=False), "minify_js=JSMinify(mangle=True, fold=False)", id="on-no-fold"),
        pytest.param(JSMinify(mangle=False), "minify_js=JSMinify(mangle=False, fold=True)", id="on-no-mangle"),
    ],
)
def test_minify_repr_includes_minify_js(options: JSMinify | None, text: str) -> None:
    assert repr(Minify(minify_js=options)).endswith(f", {text}, minify_css=None)")


def test_minify_equality_accounts_for_minify_js() -> None:
    assert Minify(minify_js=JSMinify()) == Minify(minify_js=JSMinify())
    assert Minify(minify_js=JSMinify()) != Minify()
    assert Minify(minify_js=JSMinify(fold=False)) != Minify(minify_js=JSMinify())


def test_minify_hash_distinguishes_minify_js() -> None:
    assert hash(Minify(minify_js=JSMinify())) != hash(Minify())
    assert hash(Minify(minify_js=JSMinify())) == hash(Minify(minify_js=JSMinify()))


def test_minify_js_rejects_non_jsminify() -> None:
    with pytest.raises(TypeError, match="minify_js must be a JSMinify or None"):
        Minify(minify_js=123)  # ty: ignore[invalid-argument-type]  # wrong type on purpose, to test the guard


_DOC: Final[str] = (
    "<html><head><title>Hi</title></head><body><p class='lead'>one</p>  <p>two</p><!--note--></body></html>"
)


def test_minify_collapses_whitespace_and_omits_tags() -> None:
    assert minify(_DOC) == "<title>Hi</title><p class=lead>one</p> <p>two"


def test_minify_none_matches_default_options() -> None:
    assert minify(_DOC) == minify(_DOC, Minify())


def test_clean_reexports_minify_config() -> None:
    assert CleanMinify is Minify


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(Minify(omit_optional_tags=False), True, id="keep-optional-tags"),
        pytest.param(Minify(collapse_whitespace=False), True, id="keep-whitespace"),
        pytest.param(Minify(unquote_attributes=False), True, id="keep-quotes"),
        pytest.param(Minify(strip_comments=False), True, id="keep-comments"),
    ],
)
def test_minify_options_thread_through(options: Minify, *, expected: bool) -> None:
    assert (minify(_DOC, options) != minify(_DOC)) is expected


def test_minify_keep_comments_retains_comment() -> None:
    assert "<!--note-->" in minify(_DOC, Minify(strip_comments=False))


def test_minify_keep_optional_tags_retains_html_and_body() -> None:
    out = minify(_DOC, Minify(omit_optional_tags=False))
    assert out.startswith("<html><head>")
    assert "<body>" in out


def test_minify_keep_quotes_retains_attribute_quotes() -> None:
    assert 'class="lead"' in minify(_DOC, Minify(unquote_attributes=False))


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(_DOC, id="document"),
        pytest.param("<pre>  keep   spaces  </pre>", id="preformatted"),
        pytest.param("<pre>&#xd;x</pre>", id="preformatted-carriage-return"),
        pytest.param('<p title="a&#xd;b">x</p>', id="attribute-carriage-return"),
        pytest.param("<p>one</p><script>let x = 1 + 2;</script>", id="raw-text"),
        pytest.param("<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>", id="table"),
        pytest.param("<!doctype html><html><body><p>x</p></body></html>", id="doctyped"),
        pytest.param("", id="empty"),
    ],
)
def test_minify_is_idempotent(source: str) -> None:
    once = minify(source)
    assert minify(once) == once


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<p><b>a</b><!--c--></p>", "<p><b>a</b></p></body></html>", id="p-formatting-then-comment"),
        pytest.param("<p><b>a</b></p><!--c-->", "<p><b>a</b></p></body></html>", id="p-comment-after-end"),
        pytest.param("<em></em><!-- c -->", "<em></em></body></html>", id="body-formatting-then-comment"),
        pytest.param("<div><font></div><!-- end -->", "<div><font></font></div></body></html>", id="div-font"),
        pytest.param("<b></body><!-- end -->", "<b></b></body></html>", id="body-end-then-comment"),
        pytest.param("<nobr></body><!--d-->", "<nobr></nobr></body></html>", id="body-end-nobr"),
        pytest.param("<ul><font></ul><!-- end -->", "<ul><font></font></ul></body></html>", id="ul-font"),
        pytest.param("<body><b>x</b></body><!--c-->", "<b>x</b></body></html>", id="body-b-comment-after"),
        pytest.param("<div><p><b>a</b><!--c--></p></div>", "<div><p><b>a</b></p></div></body></html>", id="nested-p"),
        pytest.param(
            "<table><td><b></table><!-- c -->",
            "<table><tbody><tr><td><b></b></td></tr></tbody></table></body></html>",
            id="table-cell",
        ),
    ],
)
def test_minify_keeps_end_tag_guarded_by_trailing_formatting(source: str, expected: str) -> None:
    # a stripped comment is invisible to the reparse, so the formatting element before it still ends the parent
    once: Final = minify(source)
    assert once == expected
    assert minify(once) == once


@pytest.mark.parametrize("tag", ["pre", "listing"])
def test_minify_keeps_leading_newline_after_stripped_comment(tag: str) -> None:
    # the parser drops the newline that opens the content, so the stripped comment must not hide the one before it
    once: Final = minify(f"<{tag}><!--c-->\nx</{tag}>")
    assert once == f"<{tag}>\n\nx</{tag}>"
    assert minify(once) == once


def test_minify_is_idempotent_over_seeded_documents() -> None:
    tokens = (
        "x",
        " ",
        "<!-- c -->",
        "<p>",
        "</p>",
        "<li>",
        "</li>",
        "<b>",
        "</b>",
        "<i>",
        "</i>",
        "<em>",
        "</em>",
        "<font>",
        "</font>",
        "<nobr>",
        "</nobr>",
        "<div>",
        "</div>",
        "<ul>",
        "</ul>",
        "<table>",
        "<td>",
        "</table>",
        "<span>",
        "</span>",
        "<body>",
        "</body>",
        "</html>",
        "<title>t</title>",
        "<base href=x>",
        "<noscript>",
        "</noscript>",
    )
    rng: Final = random.Random(4)  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # a corpus seed
    documents: Final = ["".join(rng.choice(tokens) for _ in range(rng.randint(1, 12))) for _ in range(2000)]
    failures: Final = [
        f"{document!r}\n  once:  {minify(document)!r}\n  twice: {minify(minify(document))!r}"
        for document in documents
        if _plain_roundtrips(document) and not _minify_idempotent(document, Minify())
    ]
    assert not failures, f"{len(failures)} non-idempotent\n\n" + "\n\n".join(failures[:5])


def test_minify_shrinks_documents() -> None:
    big = "<!doctype html><html><body>" + "<p class='x'>  text  </p>\n" * 200 + "</body></html>"
    assert len(minify(big)) < len(big)


@pytest.mark.parametrize(
    ("tag", "sibling"),
    [
        pytest.param("rt", "rt", id="ruby-text"),
        pytest.param("rt", "rp", id="ruby-parenthesis"),
        pytest.param("optgroup", "optgroup", id="option-group"),
    ],
)
def test_minify_detached_root_bounds_scope_inner(tag: str, sibling: str) -> None:
    root: Final = Element("div", children=[Element(tag, children=[Text("a")]), Element(sibling, children=[Text("b")])])
    content: Final = f"<{tag}>a</{tag}><{sibling}>b"
    assert root.serialize(Html(layout=Minify()), inner=True) == content


@pytest.mark.parametrize(
    ("tag", "sibling"),
    [
        pytest.param("rt", "rt", id="ruby-text"),
        pytest.param("rt", "rp", id="ruby-parenthesis"),
        pytest.param("optgroup", "optgroup", id="option-group"),
    ],
)
def test_minify_detached_root_bounds_scope_outer(tag: str, sibling: str) -> None:
    root: Final = Element("div", children=[Element(tag, children=[Text("a")]), Element(sibling, children=[Text("b")])])
    content: Final = f"<{tag}>a</{tag}><{sibling}>b"
    assert root.serialize(Html(layout=Minify())) == f"<div>{content}</div>"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<plaintext>hello <b>", "<plaintext>hello <b>", id="document"),
        pytest.param("<div><plaintext>x", "<div><plaintext>x", id="inside-div"),
        pytest.param("<template><plaintext>x</template>y", "<template><plaintext>x</template>y", id="in-template"),
    ],
)
def test_minify_plaintext_leaves_the_rest_open(source: str, expected: str) -> None:
    # the parser reads everything after <plaintext> as its text, so no end tag may follow
    assert minify(source) == expected


def test_minify_plaintext_root_omits_end_tag() -> None:
    root: Final = Element("plaintext", children=[Text("x")])
    assert root.serialize(Html(layout=Minify())) == "<plaintext>x"


_TRANSITIONAL: Final[str] = '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">'


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<p>x</p><table></table>", "<p>x</p><table></table>", id="no-doctype-keeps-end-tag"),
        pytest.param(
            f"{_TRANSITIONAL}<p>x</p><table></table>",
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN"><p>x</p><table></table>',
            id="quirky-doctype-keeps-end-tag",
        ),
        pytest.param(
            f"{_TRANSITIONAL}<p>x<table></table>",
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN"><p>x<table></table>',
            id="quirky-doctype-keeps-nested-table",
        ),
        pytest.param(
            "<!DOCTYPE html><p>x</p><table></table>", "<!DOCTYPE html><p>x<table></table>", id="no-quirks-omits-end-tag"
        ),
    ],
)
def test_minify_p_before_table(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<!DOCTYPE html>", "<!DOCTYPE html>", id="name-only"),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">',
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">',
            id="public-and-system",
        ),
        pytest.param(
            '<!DOCTYPE html SYSTEM "about:legacy-compat">', '<!DOCTYPE html SYSTEM "about:legacy-compat">', id="system"
        ),
        pytest.param('<!DOCTYPE html PUBLIC "" "">', '<!DOCTYPE html PUBLIC "" "">', id="empty-identifiers"),
        pytest.param('<!DOCTYPE html PUBLIC "a\'b">', '<!DOCTYPE html PUBLIC "a\'b">', id="apostrophe"),
        pytest.param("<!DOCTYPE html PUBLIC 'a\"b'>", "<!DOCTYPE html PUBLIC 'a\"b'>", id="double-quote"),
        pytest.param("<!DOCTYPE html bogus>", "<!DOCTYPE html x>", id="forced-quirks-name"),
        pytest.param('<!DOCTYPE html PUBLIC "a>', '<!DOCTYPE html PUBLIC "a>', id="forced-quirks-public"),
        pytest.param('<!DOCTYPE html PUBLIC "a" "b>', '<!DOCTYPE html PUBLIC "a" "b>', id="forced-quirks-system"),
        pytest.param('<!DOCTYPE html SYSTEM "b>', '<!DOCTYPE html SYSTEM "b>', id="forced-quirks-system-only"),
        pytest.param("<!DOCTYPE foo bogus>", "<!DOCTYPE foo>", id="quirky-name-needs-no-force"),
    ],
)
def test_minify_doctype(source: str, expected: str) -> None:
    assert minify(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(f"{_TRANSITIONAL}<p>x<table><tr><td>a</td></tr></table>", id="transitional"),
        pytest.param("<!DOCTYPE html bogus><p>x<table>", id="forced-quirks"),
        pytest.param('<!DOCTYPE html PUBLIC "a" "b><p>x<table>', id="forced-quirks-system"),
    ],
)
def test_minify_quirks_document_reparses_same(source: str) -> None:
    once: Final = minify(source)
    assert (parse(once).html, minify(once)) == (parse(source).html, once)


def test_minify_document_without_doctype_keeps_end_tag() -> None:
    document: Final = parse("<!DOCTYPE html><p>x</p><table></table>")
    _ = document.children[0].extract()
    assert "x</p><table>" in document.serialize(Html(layout=Minify()))


def test_minify_document_with_adopted_quirky_doctype_keeps_end_tag() -> None:
    document: Final = parse("<!DOCTYPE html><p>x</p><table></table>")
    document.children[0].replace_with(_doctype("<!DOCTYPE foo>"))
    assert "x</p><table>" in document.serialize(Html(layout=Minify()))


def _doctype(source: str) -> Doctype:
    node = parse(source).children[0]
    assert isinstance(node, Doctype)
    return node


def test_minify_xml_doctype_system_identifier_stays_out_of_markup() -> None:
    # an XML SystemLiteral takes a ">", which ends an HTML doctype and hands the rest of the literal to the parser
    document: Final = parse_xml('<!DOCTYPE html SYSTEM "urn:k>lm<i>q</i>"><html xmlns="http://www.w3.org/1999/xhtml"/>')
    assert document.select("i") == []
    minified: Final = document.serialize(Html(layout=Minify()))
    assert minified == "<!DOCTYPE html><html xmlns=http://www.w3.org/1999/xhtml></html>"
    assert parse(minified).select("i") == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param('<!DOCTYPE html SYSTEM "k>lm"><html/>', "<!DOCTYPE html><html></html>", id="system"),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "k>lm"><html/>',
            "<!DOCTYPE html><html></html>",
            id="public-and-system",
        ),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//IETF//DTD HTML//EN" "k>lm"><html><body><p>x</p><table/></body></html>',
            "<!DOCTYPE html x><html><body><p>x</p><table></table></body></html>",
            id="quirky-public-stays-quirky",
        ),
        pytest.param('<!DOCTYPE root SYSTEM "k>lm"><root/>', "<!DOCTYPE root x><root></root>", id="quirky-name"),
        pytest.param(
            "<!DOCTYPE html SYSTEM 'a\"b'><html/>", "<!DOCTYPE html SYSTEM 'a\"b'><html></html>", id="double-quote-fits"
        ),
    ],
)
def test_minify_xml_doctype(source: str, expected: str) -> None:
    assert parse_xml(source).serialize(Html(layout=Minify())) == expected


@pytest.mark.parametrize(
    ("public_id", "system_id", "expected"),
    [
        pytest.param("k>lm", None, "<!DOCTYPE html>", id="public-gt"),
        pytest.param("a", "m>no", "<!DOCTYPE html>", id="system-gt"),
        pytest.param("a\"b'c", "d", "<!DOCTYPE html>", id="public-both-quotes"),
        pytest.param(None, "a'b\"c", "<!DOCTYPE html>", id="system-both-quotes"),
        pytest.param("-//IETF//DTD HTML//EN", "k>lm", "<!DOCTYPE html x>", id="quirky-public"),
        pytest.param("a'b", 'c"d', "<!DOCTYPE html PUBLIC \"a'b\" 'c\"d'>", id="one-quote-each-fits"),
    ],
)
def test_minify_built_doctype_identifier_html_cannot_spell(
    public_id: str | None, system_id: str | None, expected: str
) -> None:
    reduced: Final = _doctype("<!DOCTYPE html>").__reduce__()
    assert isinstance(reduced, tuple)
    reconstruct, (kind, *_) = reduced
    # the pickle hook rebuilds the identifiers as given, so it reaches the ones neither parser stores
    node: Final = reconstruct(kind, ("html", public_id, system_id), [])
    assert node.serialize(Html(layout=Minify())) == expected


def test_minify_forced_quirks_document_with_adopted_xml_doctype_stays_quirky() -> None:
    document: Final = parse("<!DOCTYPE html bogus><p>x</p><table></table>")
    document.children[0].replace_with(parse_xml('<!DOCTYPE html SYSTEM "k>lm"><html/>').children[0])
    minified: Final = document.serialize(Html(layout=Minify()))
    assert minified == "<!DOCTYPE html x><p>x</p><table></table>"
    assert parse(minified).serialize(Html(layout=Minify())) == minified


def test_minify_element_in_no_quirks_tree_omits_end_tag() -> None:
    body: Final = parse("<!DOCTYPE html><body><p>x</p><table></table>").select_one("body")
    assert isinstance(body, Element)
    assert body.serialize(Html(layout=Minify())) == "<body><p>x<table></table></body>"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("<p>x<table><p>c</table>E", id="p-in-p-quirks"),
        pytest.param("<p><table></p>", id="p-end-in-table"),
        pytest.param("<p><table><div>", id="block-start-in-p"),
        pytest.param("<p><table><h1>", id="heading-start-in-p"),
        pytest.param("<p><table><dt>", id="dt-start-in-p"),
        pytest.param("<li><table><li>", id="li-in-li"),
        pytest.param("<dt><table><dt>", id="dt-in-dt"),
        pytest.param("<h1><table><h1>", id="h1-in-h1"),
        pytest.param("<a><table><a>", id="a-in-a"),
        pytest.param("<nobr><table><nobr>", id="nobr-in-nobr"),
        pytest.param("<button><table><button>", id="button-in-button"),
        pytest.param("<option><table><option>", id="option-in-option"),
        pytest.param("<select><table><select>", id="select-in-select"),
        pytest.param("<select><table><input>", id="input-in-select"),
    ],
)
def test_minify_keeps_a_tree_a_reparse_would_nest_differently(source: str) -> None:
    # the minified form nests an element inside its own kind, which the reparse closes; the markup comes back as written
    assert minify(source) == source
    assert parse(minify(source)).equals(parse(source))


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("<ul>\n<li>a\n<ul>\n<li>b\n</ul>\n</ul>", id="list-behind-list"),
        pytest.param("<p>\na\n<table>\n<td>\n<p>\nb\n</td>\n</table>", id="p-behind-cell"),
        pytest.param("<a>\n<table>\n<td>\n<a>x</a>\n</td>\n</table>\n</a>", id="a-behind-cell"),
        pytest.param("<p>\nx\n</p>\n<li>\ny\n</li>", id="sibling-blocks"),
    ],
)
def test_minify_still_minifies_a_nesting_a_reparse_builds(source: str) -> None:
    # a scope boundary between the two elements, or no element of the kind above the other, keeps the reparse from
    # closing anything, so the whitespace still folds
    assert "\n" not in minify(source)


def test_minify_of_misnested_formatting_still_minifies() -> None:
    assert minify("<b><i>x</b></i>\n<p>\n y\n</p>") == "<b><i>x</i></b> <p> y "


def test_minify_walks_every_kind_the_nesting_check_reads() -> None:
    # every element the check classifies sits in one document, so each of its branches runs on a tree that minifies
    source = (
        "<div><p>a<h2>b</h2><h3>c</h3><h4>d</h4><h5>e</h5><h6>f</h6><dl><dd>g<dt>h</dt></dl></p>"
        "<ol><li>i</li></ol><ul><li>j</li></ul><a><nobr>k</nobr></a><button>l</button>"
        "<select><optgroup><option>m</option></optgroup><input></select>"
        "<textarea>n</textarea><keygen><svg><title>o</title><desc>p</desc><foreignObject>q</foreignObject></svg>"
        "<math><mi>r</mi><mo>s</mo><mn>t</mn><ms>u</ms><mtext>v</mtext><annotation-xml>w</annotation-xml></math>"
        "<table><td><applet>x</applet><object>y</object><marquee>z</marquee><template>x</template></td></table></div>"
    )
    assert "<div>" in minify(source)


@pytest.mark.parametrize("count", [pytest.param(0, id="short"), pytest.param(80, id="long")])
def test_minify_keeps_a_heading_nested_past_inline_content(count: int) -> None:
    # a heading start tag closes only the current node, so the span between the two headings keeps the nesting; the
    # long page holds more elements than the check's first buffer, so the buffer grows
    source = f"<h1><span><h2>x</h2></span></h1>{'<b>x</b>' * count}"
    assert parse(minify(source)).equals(parse(source))


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param("<table><tr>c<td>E", id="table-row"),
        pytest.param("<table><template>c</template>E", id="template"),
        pytest.param("<p>a<b>b<i>c</b>E", id="formatting"),
        pytest.param("<noscript><p>c</noscript>E", id="noscript"),
    ],
)
@pytest.mark.parametrize(
    "depth", [pytest.param(_PARSER_DEPTH + 88, id="deep"), pytest.param(_PARSER_DEPTH * 2, id="x2")]
)
def test_minify_past_the_depth_cap_returns_markup_as_written(tail: str, depth: int) -> None:
    # a node the cap moved has no minified form that reparses to the same tree, so the markup comes back unchanged
    markup = f"{'<div> ' * depth}{tail}"
    assert minify(markup) == markup


def test_minify_below_the_depth_cap_still_minifies() -> None:
    assert minify(f"{'<div> ' * (_PARSER_DEPTH - 88)}x") != f"{'<div> ' * (_PARSER_DEPTH - 88)}x"
