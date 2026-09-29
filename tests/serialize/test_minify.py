"""Unit tests for the serialize(layout=Minify(...)) transforms and the Minify options object.

Each transform is round-trip safe: the minified output reparses to the same tree.
The exhaustive corpus check of that property lives in test_minify_roundtrip.py; here
every individual rule and option is pinned with an explicit expected string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Doctype, Element, Formatter, Html, Minify, Text, parse, parse_fragment
from turbohtml.clean import CSSMinify, minify

if TYPE_CHECKING:
    from wpt_tree_corpus import WptHtmlTreeCorpus
from turbohtml.clean import JSMinify
from turbohtml.clean import Minify as CleanMinify


def frag(
    source: str,
    *,
    collapse_whitespace: bool = True,
    omit_optional_tags: bool = True,
    unquote_attributes: bool = True,
    strip_comments: bool = True,
) -> str:
    """Minify source parsed in a <div> fragment context, returning the inner markup.

    A <div> is never an optional tag and carries no attributes, so it survives every
    transform unchanged and strips cleanly, leaving just the minified content.
    """
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


def test_minify_defaults_all_on() -> None:
    m = Minify()
    assert (m.collapse_whitespace, m.omit_optional_tags, m.unquote_attributes, m.strip_comments) == (
        True,
        True,
        True,
        True,
    )


def test_minify_flags_independent() -> None:
    m = Minify(collapse_whitespace=False, omit_optional_tags=False, unquote_attributes=False, strip_comments=False)
    assert (m.collapse_whitespace, m.omit_optional_tags, m.unquote_attributes, m.strip_comments) == (
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


def test_collapse_runs_to_single_space() -> None:
    assert frag("a   \t\n  b", omit_optional_tags=False) == "a b"


def test_collapse_preserves_pre() -> None:
    assert frag("<pre>  a   b  </pre>", omit_optional_tags=False) == "<pre>  a   b  </pre>"


def test_collapse_preserves_textarea() -> None:
    assert frag("<textarea>  a   b  </textarea>", omit_optional_tags=False) == "<textarea>  a   b  </textarea>"


def test_collapse_off_keeps_whitespace() -> None:
    assert frag("a   b", collapse_whitespace=False, omit_optional_tags=False) == "a   b"


def test_collapse_merges_across_stripped_comment() -> None:
    assert frag("a <!--x--> b", omit_optional_tags=False) == "a b"


def test_collapse_keeps_space_around_element() -> None:
    assert frag("a <b>x</b> b", omit_optional_tags=False) == "a <b>x</b> b"


def test_collapse_escapes_specials_in_text() -> None:
    # the fused collapse still escapes &, < and > between the folded whitespace runs
    assert frag("a &  b   <  c & d > e", omit_optional_tags=False) == "a &amp; b &lt; c &amp; d &gt; e"


def test_collapse_escapes_nbsp_under_whatwg() -> None:
    # a non-break space is not ASCII whitespace, so WHATWG escapes it rather than folding
    assert frag("a\u00a0\u00a0b", omit_optional_tags=False) == "a&nbsp;&nbsp;b"


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


def test_unquote_safe_value() -> None:
    assert frag("<a href='x'>t</a>", omit_optional_tags=False) == "<a href=x>t</a>"


def test_unquote_keeps_quotes_on_space() -> None:
    assert frag("<a title='a b'>t</a>", omit_optional_tags=False) == '<a title="a b">t</a>'


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a=b", '<a x="a=b">', id="equals"),
        pytest.param("a<b", '<a x="a<b">', id="lt"),
        pytest.param("a>b", '<a x="a>b">', id="gt"),
        pytest.param("a`b", '<a x="a`b">', id="backtick"),
        pytest.param("a&b", '<a x="a&amp;b">', id="amp"),
        pytest.param("ab/", '<a x="ab/">', id="trailing-slash"),
    ],
)
def test_unquote_keeps_quotes_on_unsafe(value: str, expected: str) -> None:
    # the value contains a character that bars unquoting, so it stays quoted (WHATWG
    # attribute escaping leaves < and > literal; only &, " and nbsp are rewritten)
    out = frag(f'<a x="{value}">', omit_optional_tags=False)
    assert out.startswith(expected)


def test_unquote_empty_value_becomes_bare_name() -> None:
    assert frag('<input disabled="">', omit_optional_tags=False) == "<input disabled>"


def test_unquote_off_keeps_quotes() -> None:
    assert frag("<a href='x'>t</a>", unquote_attributes=False, omit_optional_tags=False) == '<a href="x">t</a>'


def test_strip_comment() -> None:
    assert frag("<p>a</p><!--c--><p>b</p>", omit_optional_tags=False) == "<p>a</p><p>b</p>"


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
            "<ruby><svg><foreignObject><rt></rt><rp>x</foreignObject></svg></ruby>",
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


_PHRASING_PARENTS: Final = ["span", "label", "q", "sup", "ruby", "option", "slot", "picture", "my-el", "del"]


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


def test_omit_keeps_p_end_inside_formatting() -> None:
    # the <p> ends inside a reconstructed <i>, so dropping </p> would change the reparse
    out = frag("<i>a<p>b</i>", strip_comments=False)
    assert "</p>" in out


def test_omit_keeps_p_end_before_formatting_sibling() -> None:
    out = frag("<div><p>a</p><b>x</b></div>", strip_comments=False)
    assert "</p>" in out


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


def test_omit_off_keeps_all_tags() -> None:
    assert frag("<p>a</p><p>b</p>", omit_optional_tags=False) == "<p>a</p><p>b</p>"


def test_collapse_all_whitespace_characters() -> None:
    # space, tab, line feed, form feed and carriage return all fold to one space
    assert frag("a\tb\nc\x0cd\re f", omit_optional_tags=False) == "a b c d e f"


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


def test_void_element_minified() -> None:
    assert frag("<p>a<br>b</p>", omit_optional_tags=False) == "<p>a<br>b</p>"


def test_empty_element_minified() -> None:
    assert frag("<div></div>", omit_optional_tags=False) == "<div></div>"


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


def test_omit_dd_end_kept_before_text() -> None:
    assert frag("<dl><dd>a</dd>x</dl>", strip_comments=False) == "<dl><dd>a</dd>x</dl>"


def test_body_start_kept_before_comment() -> None:
    assert parse("<body><!--c-->x</body>").serialize(Html(layout=Minify(strip_comments=False))) == "<body><!--c-->x"


@pytest.mark.parametrize("tag", [pytest.param(t, id=t) for t in ("meta", "link", "script", "style", "template")])
def test_body_start_kept_before_head_element(tag: str) -> None:
    assert parse(f"<body><{tag}></{tag}>x</body>").serialize(Html(layout=Minify())).startswith(f"<body><{tag}")


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


def test_rawtext_script_preserved() -> None:
    assert frag("<script>a  <  b</script>", omit_optional_tags=False) == "<script>a  <  b</script>"


_CSS = CSSMinify()


def css_frag(source: str, *, minify_css: CSSMinify | None = _CSS, unquote_attributes: bool = True) -> str:
    """Minify source in a <div> fragment with the CSS pass, returning the inner markup."""
    layout = Minify(minify_css=minify_css, unquote_attributes=unquote_attributes)
    out = parse_fragment(source, "div").serialize(Html(layout=layout))
    assert out.startswith("<div>")
    assert out.endswith("</div>")
    return out[len("<div>") : -len("</div>")]


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


def _plain_roundtrips(source: str) -> bool:
    once = parse(source).serialize()
    return once == parse(once).serialize()


def _minify_idempotent(source: str, layout: Minify) -> bool:
    once = parse(source).serialize(Html(layout=layout))
    return once == parse(once).serialize(Html(layout=layout))


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
        + "".join(section.format(i=index) for index in range(500))
        + "</body></html>"
    )
    layout = Minify()
    once = parse(big).serialize(Html(layout=layout))
    assert once == parse(once).serialize(Html(layout=layout))
    assert len(once) < len(parse(big).serialize())  # minification actually shrinks the document


_SCRIPT = "function f(){ var longName = 1 + 2 ; return longName }"


def script(source: str, minify_js: JSMinify | None = None) -> str:
    """Serialize source as the single child of a <script> under Minify(minify_js=...),
    returning just the script element so the assertion is the minified (or verbatim) body."""
    out = parse_fragment(f"<script>{source}</script>", "div").serialize(Html(layout=Minify(minify_js=minify_js)))
    assert out.startswith("<div><script>")
    assert out.endswith("</script></div>")
    return out[len("<div>") : -len("</div>")]


def test_scripts_untouched_without_minify_js() -> None:
    # the default Minify leaves JavaScript exactly as written
    assert script(_SCRIPT) == f"<script>{_SCRIPT}</script>"


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


_DOC = "<html><head><title>Hi</title></head><body><p class='lead'>one</p>  <p>two</p><!--note--></body></html>"


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
        pytest.param("<p>one</p><script>let x = 1 + 2;</script>", id="raw-text"),
        pytest.param("<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>", id="table"),
        pytest.param("<!doctype html><html><body><p>x</p></body></html>", id="doctyped"),
        pytest.param("", id="empty"),
    ],
)
def test_minify_is_idempotent(source: str) -> None:
    once = minify(source)
    assert minify(once) == once


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
@pytest.mark.parametrize("inner", [False, True], ids=["outer", "inner"])
def test_minify_detached_root_bounds_scope(tag: str, sibling: str, *, inner: bool) -> None:
    root: Final = Element("div", children=[Element(tag, children=[Text("a")]), Element(sibling, children=[Text("b")])])
    content: Final = f"<{tag}>a</{tag}><{sibling}>b"
    assert root.serialize(Html(layout=Minify()), inner=inner) == (content if inner else f"<div>{content}</div>")


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


_TRANSITIONAL: Final = '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">'


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


def _doctype(source: str) -> Doctype:
    node = parse(source).children[0]
    assert isinstance(node, Doctype)
    return node


def test_minify_document_without_doctype_keeps_end_tag() -> None:
    document: Final = parse("<!DOCTYPE html><p>x</p><table></table>")
    _ = document.children[0].extract()
    assert "x</p><table>" in document.serialize(Html(layout=Minify()))


def test_minify_document_with_adopted_quirky_doctype_keeps_end_tag() -> None:
    document: Final = parse("<!DOCTYPE html><p>x</p><table></table>")
    document.children[0].replace_with(_doctype("<!DOCTYPE foo>"))
    assert "x</p><table>" in document.serialize(Html(layout=Minify()))


def test_minify_element_in_no_quirks_tree_omits_end_tag() -> None:
    body: Final = parse("<!DOCTYPE html><body><p>x</p><table></table>").select_one("body")
    assert isinstance(body, Element)
    assert body.serialize(Html(layout=Minify())) == "<body><p>x<table></table></body>"
