from __future__ import annotations

import pytest

from turbohtml import Element, Text, parse_fragment


def _boom(url: str) -> str:
    msg = f"nope: {url}"
    raise ValueError(msg)


def _rewritten(html: str, replace: object) -> str:
    root = parse_fragment(html)
    root.rewrite_links(replace)  # ty: ignore[invalid-argument-type]
    return root.inner_html


def test_replacement_string_is_substituted() -> None:
    assert _rewritten('<a href="a">x</a>', lambda url: url.upper()) == '<a href="A">x</a>'


def test_returning_none_leaves_the_link_unchanged() -> None:
    assert _rewritten('<a href="a">x</a>', lambda _url: None) == '<a href="a">x</a>'


def test_returning_the_same_string_is_not_a_mutation() -> None:
    assert _rewritten('<a href="a">x</a>', lambda url: url) == '<a href="a">x</a>'


def test_only_some_candidates_change() -> None:
    out = _rewritten('<img srcset="a.png 1x, b.png 2x">', lambda url: "X" if url == "a.png" else None)
    assert out == '<img srcset="X 1x, b.png 2x">'


def test_a_shorter_replacement_shrinks_the_value() -> None:
    assert _rewritten('<a href="a-long-url">x</a>', lambda _url: "z") == '<a href="z">x</a>'


def test_replace_receives_each_url() -> None:
    seen: list[str] = []

    def collect(url: str) -> None:
        seen.append(url)

    _rewritten('<a href="h" ping="p1 p2"><img src="i"></a>', collect)
    assert seen == ["h", "p1", "p2", "i"]


def test_a_non_string_non_none_result_is_a_type_error() -> None:
    with pytest.raises(TypeError, match="must return str or None"):
        _rewritten('<a href="a">x</a>', lambda _url: 42)


def test_an_exception_from_replace_propagates() -> None:
    with pytest.raises(ValueError, match="nope"):
        _rewritten('<a href="a">x</a>', _boom)


def test_an_exception_during_a_meta_refresh_rewrite_stops_the_walk() -> None:
    # the meta content fails first, so the element's remaining attributes are not visited
    with pytest.raises(ValueError, match="nope: next"):
        _rewritten('<meta http-equiv=refresh content="5; url=next.html" id=m>', _boom)


def test_an_exception_on_the_first_of_several_style_texts_stops_the_walk() -> None:
    style = Element("style")
    style.append(Text("a{background:url(one.png)}"))
    style.append(Text("b{background:url(two.png)}"))
    seen: list[str] = []

    def boom(url: str) -> str:
        seen.append(url)
        msg = "stop"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="stop"):
        style.rewrite_links(boom)
    assert seen == ["one.png"]  # the second text node was never reached


def test_rewrite_links_requires_a_callable() -> None:
    with pytest.raises(TypeError, match="expected a callable"):
        parse_fragment("<a href=a>x</a>").rewrite_links("not callable")  # ty: ignore[invalid-argument-type]
