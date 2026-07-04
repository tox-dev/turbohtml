"""Unit tests for the serialize(layout=Minify(...)) transforms and the Minify options object.

Each transform is round-trip safe: the minified output reparses to the same tree.
The exhaustive corpus check of that property lives in test_minify_roundtrip.py; here
every individual rule and option is pinned with an explicit expected string.
"""

from __future__ import annotations

import pytest

from turbohtml import Element, Formatter, Html, Minify, Text, parse, parse_fragment


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
        "minify_js=None, minify_css=False)"
    )
    assert repr(Minify(collapse_whitespace=False, unquote_attributes=False, strip_comments=False)) == (
        "Minify(collapse_whitespace=False, omit_optional_tags=True, unquote_attributes=False, strip_comments=False, "
        "minify_js=None, minify_css=False)"
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
        Minify(True)  # ty: ignore[too-many-positional-arguments]  # noqa: FBT003  # keyword-only


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
        pytest.param(
            "<table><caption>c</caption><tbody><tr><td>a</td></tr></tbody></table>",
            "<table><caption>c<tbody><tr><td>a</table>",
            id="caption-before-section",
        ),
        pytest.param(
            "<table><caption>c</caption></table>",
            "<table><caption>c</table>",
            id="caption-last",
        ),
        pytest.param(
            "<table><colgroup><col></colgroup><tbody><tr><td>a</td></tr></tbody></table>",
            "<table><colgroup><col><tbody><tr><td>a</table>",
            id="colgroup-before-section",
        ),
        pytest.param(
            "<table><colgroup><col></colgroup><colgroup><col></colgroup><tr><td>a</td></tr></table>",
            "<table><colgroup><col><colgroup><col><tbody><tr><td>a</table>",
            id="colgroup-before-colgroup",
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


@pytest.mark.parametrize(
    ("element", "follow"),
    [
        pytest.param("caption", " ", id="caption-before-whitespace"),
        pytest.param("caption", "<!--c-->", id="caption-before-comment"),
        pytest.param("colgroup", " ", id="colgroup-before-whitespace"),
        pytest.param("colgroup", "<!--c-->", id="colgroup-before-comment"),
    ],
)
def test_caption_colgroup_end_kept_before_whitespace_or_comment(element: str, follow: str) -> None:
    # an inserted whitespace text node or a comment after the element would reattach
    # inside it on reparse, so the end tag must survive; every other following node is a
    # table section the parser's own close reconstructs
    inner = "<col>" if element == "colgroup" else "c"
    source = f"<table><{element}>{inner}</{element}>{follow}<tbody><tr><td>a</table>"
    once = parse(source).serialize(Html(layout=Minify(strip_comments=False)))
    assert f"</{element}>" in once
    assert parse(once).html == parse(source).html
    assert parse(once).serialize(Html(layout=Minify(strip_comments=False))) == once


@pytest.mark.parametrize(
    "name",
    [
        "allowfullscreen",
        "async",
        "autofocus",
        "autoplay",
        "checked",
        "controls",
        "default",
        "defer",
        "disabled",
        "formnovalidate",
        "inert",
        "ismap",
        "itemscope",
        "loop",
        "multiple",
        "muted",
        "nomodule",
        "novalidate",
        "open",
        "playsinline",
        "readonly",
        "required",
        "reversed",
        "selected",
    ],
)
def test_boolean_attribute_value_collapses_to_bare_name(name: str) -> None:
    # a boolean attribute renders on presence alone, so a value that repeats the name is
    # redundant; the bare form reparses to an empty value that re-minifies to itself
    source = f'<div {name}="{name}">t</div>'
    out = frag(source, omit_optional_tags=False)
    assert out == f"<div {name}>t</div>"
    assert frag(out, omit_optional_tags=False) == out  # the bare form is a re-minify fixpoint


def test_boolean_attribute_value_collapse_is_case_insensitive() -> None:
    assert frag('<input checked="CHECKED">', omit_optional_tags=False) == "<input checked>"


def test_boolean_attribute_empty_value_collapses_to_bare_name() -> None:
    assert frag('<input disabled="">', omit_optional_tags=False) == "<input disabled>"


def test_boolean_attribute_keeps_value_when_it_differs_from_name() -> None:
    # only a value repeating the name (or empty) is redundant; any other value is preserved
    assert frag('<input disabled="true">', omit_optional_tags=False) == "<input disabled=true>"


def test_non_boolean_attribute_value_matching_name_is_kept() -> None:
    # title is not a boolean attribute, so title="title" carries a real value and stays
    assert frag('<a title="title">t</a>', omit_optional_tags=False) == "<a title=title>t</a>"


def test_attribute_value_same_length_as_name_but_different_is_kept() -> None:
    # a value the same length as the name but not repeating it is a real value: no collapse
    assert frag('<a class="claYY">t</a>', omit_optional_tags=False) == "<a class=claYY>t</a>"


def test_boolean_attribute_collapse_needs_unquote() -> None:
    assert frag('<input checked="checked">', omit_optional_tags=False, unquote_attributes=False) == (
        '<input checked="checked">'
    )


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


def css_frag(source: str, *, minify_css: bool = True, unquote_attributes: bool = True) -> str:
    """Minify source in a <div> fragment with the CSS pass, returning the inner markup."""
    layout = Minify(minify_css=minify_css, unquote_attributes=unquote_attributes)
    out = parse_fragment(source, "div").serialize(Html(layout=layout))
    assert out.startswith("<div>")
    assert out.endswith("</div>")
    return out[len("<div>") : -len("</div>")]


def test_minify_css_defaults_off() -> None:
    assert Minify().minify_css is False


def test_minify_css_getter_round_trips() -> None:
    assert Minify(minify_css=True).minify_css is True
    assert Minify(minify_css=False).minify_css is False


def test_minify_css_equality_and_hash() -> None:
    assert Minify(minify_css=True) != Minify()
    assert Minify(minify_css=True) == Minify(minify_css=True)
    assert hash(Minify(minify_css=True)) != hash(Minify())


@pytest.mark.parametrize(
    ("minify_css", "text"),
    [pytest.param(False, "minify_css=False", id="off"), pytest.param(True, "minify_css=True", id="on")],
)
def test_minify_css_repr(minify_css: bool, text: str) -> None:  # noqa: FBT001
    assert repr(Minify(minify_css=minify_css)).endswith(f", {text})")


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
    assert css_frag(source, minify_css=False) == expected


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
    layout = Minify(minify_css=True)
    once = parse(source).serialize(Html(layout=layout))
    assert parse(once).serialize(Html(layout=layout)) == once
