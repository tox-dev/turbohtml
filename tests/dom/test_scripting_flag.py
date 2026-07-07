"""The WHATWG scripting flag: parse(scripting=True) makes <noscript> a raw-text element.

With scripting off (the default) noscript content is parsed as markup and serialized
escaped; with it on, noscript is a raw-text element whose content is a single text node
serialized verbatim, reproducing the tree a scripting browser builds.
"""

from __future__ import annotations

import pytest

from turbohtml import Html, Indent, Minify, Text, parse, parse_fragment


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
