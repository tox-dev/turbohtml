from __future__ import annotations

import pytest

from turbohtml import Element, Link, Text, parse, parse_fragment


def _urls(html: str) -> list[tuple[str, str | None, str]]:
    return [(link.element.tag, link.attribute, link.url) for link in parse_fragment(html).links()]


def test_anchor_href_is_enumerated() -> None:
    assert _urls('<a href="a/b.html">x</a>') == [("a", "href", "a/b.html")]


def test_many_elements_grow_the_snapshot() -> None:
    # The pure-C element snapshot starts at 16 slots; >16 elements exercise its regrow.
    html = "".join(f'<a href="p{index}">{index}</a>' for index in range(40))
    assert _urls(html) == [("a", "href", f"p{index}") for index in range(40)]


def test_value_is_reported_with_surrounding_whitespace_trimmed() -> None:
    assert _urls('<a href="  a/b.html  ">x</a>') == [("a", "href", "a/b.html")]


def test_carriage_return_from_char_ref_is_trimmed() -> None:
    # &#13; injects a real U+000D past the input preprocessor's CR->LF fold, and CR is
    # HTML ASCII whitespace, so "strip leading/trailing ASCII whitespace" must drop it.
    assert _urls('<a href="&#13;a/b.html&#13;">x</a>') == [("a", "href", "a/b.html")]


def test_whitespace_only_url_attribute_is_skipped() -> None:
    assert _urls('<a href="   ">x</a>') == []


def test_valueless_url_attribute_is_skipped() -> None:
    assert _urls("<a href>x</a>") == []


def test_non_link_attribute_is_ignored() -> None:
    assert _urls('<a class="c" href="u">x</a>') == [("a", "href", "u")]


@pytest.mark.parametrize(
    ("tag", "attr"),
    [
        pytest.param("img", "src", id="src"),
        pytest.param("blockquote", "cite", id="cite"),
        pytest.param("object", "data", id="data"),
        pytest.param("form", "action", id="action"),
        pytest.param("video", "poster", id="poster"),
        pytest.param("img", "longdesc", id="longdesc"),
        pytest.param("button", "formaction", id="formaction"),
        pytest.param("table", "background", id="background"),
    ],
)
def test_single_url_attributes_are_enumerated(tag: str, attr: str) -> None:
    found = _urls(f'<{tag} {attr}="u/v">t</{tag}>')
    assert (tag, attr, "u/v") in found


def test_svg_xlink_href_is_enumerated() -> None:
    found = _urls('<svg><use xlink:href="i.svg#g"></use></svg>')
    assert ("use", "xlink:href", "i.svg#g") in found


@pytest.mark.parametrize(
    "attr",
    [pytest.param("rel", id="len3"), pytest.param("type", id="len4"), pytest.param("height", id="len6")],
)
def test_same_length_non_url_attributes_are_ignored(attr: str) -> None:
    assert _urls(f'<input {attr}="not-a-url">') == []


def test_ping_is_a_whitespace_separated_list() -> None:
    assert _urls('<a href="h" ping="  p1   p2 p3 ">x</a>') == [
        ("a", "href", "h"),
        ("a", "ping", "p1"),
        ("a", "ping", "p2"),
        ("a", "ping", "p3"),
    ]


def test_ping_only_counts_on_anchor_and_area() -> None:
    assert _urls('<div ping="p1 p2">x</div>') == []
    assert ("area", "ping", "p1") in _urls('<map><area ping="p1 p2"></map>')


def test_archive_is_a_whitespace_list_on_object_and_applet() -> None:
    assert _urls('<object archive="j1.jar j2.jar"></object>') == [
        ("object", "archive", "j1.jar"),
        ("object", "archive", "j2.jar"),
    ]
    assert ("applet", "archive", "j1.jar") in _urls('<applet archive="j1.jar"></applet>')


def test_archive_is_ignored_off_object_and_applet() -> None:
    assert _urls('<div archive="j1.jar j2.jar"></div>') == []


@pytest.mark.parametrize("attr", [pytest.param("srcset", id="srcset"), pytest.param("imagesrcset", id="imagesrcset")])
def test_srcset_candidates_are_enumerated(attr: str) -> None:
    found = _urls(f'<img {attr}="a.png 1x, b.png 2x , c.png">')
    assert [url for _, _, url in found] == ["a.png", "b.png", "c.png"]


def test_srcset_with_a_trailing_comma_and_varied_whitespace() -> None:
    # the trailing comma exercises the separator skip running to the end, and \t/\n/\f the whitespace set
    found = _urls('<img srcset="a.png\t1x,\nb.png\x0c2x,">')
    assert [url for _, _, url in found] == ["a.png", "b.png"]


def test_srcset_candidate_ending_directly_at_a_comma() -> None:
    # no descriptor or space before the comma, so the URL run stops on the comma itself
    found = _urls('<img srcset="a.png,b.png 2x">')
    assert [url for _, _, url in found] == ["a.png", "b.png"]


def test_placeholder_is_not_imagesrcset() -> None:
    assert _urls('<input placeholder="javascript:not-a-url">') == []


def test_meta_refresh_url_is_enumerated() -> None:
    assert _urls('<meta http-equiv="refresh" content="5; url=next.html">') == [("meta", "content", "next.html")]


def test_meta_refresh_url_is_case_insensitive_and_unquotes() -> None:
    assert _urls('<meta http-equiv="REFRESH" content="0;URL=&#39;n.html&#39;">') == [("meta", "content", "n.html")]


def test_meta_refresh_without_url_keyword_has_no_link() -> None:
    assert _urls('<meta http-equiv="refresh" content="5">') == []


def test_meta_without_refresh_is_not_enumerated() -> None:
    assert _urls('<meta name="x" content="url=y">') == []


def test_meta_refresh_without_content_has_no_link() -> None:
    assert _urls('<meta http-equiv="refresh">') == []


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        pytest.param("background:url(bg.png)", ["bg.png"], id="unquoted"),
        pytest.param("background:url('bg.png')", ["bg.png"], id="single-quoted"),
        pytest.param("background:url(  sp.png  )", ["sp.png"], id="inner-whitespace"),
        pytest.param("background:url()", [], id="empty-url"),
        pytest.param("color:red", [], id="no-url"),
        pytest.param("background:burl(no)", [], id="not-a-url-function"),
        pytest.param("a:url(one);b:url(two)", ["one", "two"], id="two-urls"),
    ],
)
def test_css_url_in_style_attribute(style: str, expected: list[str]) -> None:
    found = _urls(f'<p style="{style}"></p>')
    assert [url for _, _, url in found] == expected


def test_css_url_double_quoted_in_single_quoted_attribute() -> None:
    assert _urls("<p style='background:url(\"bg.png\")'></p>") == [("p", "style", "bg.png")]


def test_style_element_text_is_scanned_as_css() -> None:
    found = _urls("<style>a{background:url(bg.png)} @import 'theme.css'; @media x {}</style>")
    assert [(link[0], link[1], link[2]) for link in found] == [
        ("style", None, "bg.png"),
        ("style", None, "theme.css"),
    ]


def test_empty_style_element_has_no_link() -> None:
    assert _urls("<style></style>") == []


def test_style_with_a_non_text_child_skips_it() -> None:
    # a parsed <style> is always rawtext, but a programmatic tree can give it an element child to skip
    style = Element("style")
    style.append(Element("span"))
    style.append(Text("a{background:url(bg.png)}"))
    assert [link.url for link in style.links()] == ["bg.png"]


def test_css_import_double_quoted() -> None:
    assert _urls('<style>@import "a.css";</style>') == [("style", None, "a.css")]


def test_css_import_without_string_defers_to_url_form() -> None:
    assert _urls("<style>@import url(a.css);</style>") == [("style", None, "a.css")]


def test_many_links_in_one_value_grow_the_span_buffer() -> None:
    candidates = ", ".join(f"img{index}.png {index}x" for index in range(1, 13))
    found = _urls(f'<img srcset="{candidates}">')
    assert [url for _, _, url in found] == [f"img{index}.png" for index in range(1, 13)]


def test_links_is_returned_in_document_order() -> None:
    found = _urls('<a href="1"><img src="2"></a><a href="3">x</a>')
    assert [url for _, _, url in found] == ["1", "2", "3"]


def test_links_on_a_whole_document() -> None:
    found = parse('<html><body><a href="u">x</a></body></html>').links()
    assert [(link.element.tag, link.url) for link in found] == [("a", "u")]


def test_link_is_a_named_tuple() -> None:
    (link,) = parse_fragment('<a href="u">x</a>').links()
    assert isinstance(link, Link)
    assert link == (link.element, "href", "u")
    assert link.element.attrs["href"] == "u"
