"""Node.prune, Node.remove, and Node.strip_tags: keep, drop, or unwrap the subtrees matching a CSS selector."""

from __future__ import annotations

import pytest

from turbohtml import Document, Element, parse


def _body(document: Document) -> Element:
    """The body element of a parsed document, asserted present."""
    body = document.select_one("body")
    assert body is not None
    return body


def _node(document: Document, selector: str) -> Element:
    """The first element matching selector, asserted present."""
    node = document.select_one(selector)
    assert node is not None
    return node


def test_keeps_match_and_drops_unrelated_siblings() -> None:
    document = parse("<body><nav>skip</nav><main><article>keep</article><aside>drop</aside></main></body>")
    document.prune("article")
    assert _body(document).serialize() == "<body><main><article>keep</article></main></body>"


def test_keeps_the_whole_subtree_under_a_match() -> None:
    document = parse("<main><article>text<b>bold</b><span>more</span></article><p>gone</p></main>")
    document.prune("article")
    assert (
        _body(document).serialize() == "<body><main><article>text<b>bold</b><span>more</span></article></main></body>"
    )


def test_keeps_each_of_several_matches() -> None:
    document = parse("<ul><li class=on>a</li><li>b</li><li class=on>c</li></ul>")
    document.prune("li.on")
    assert _body(document).serialize() == '<body><ul><li class="on">a</li><li class="on">c</li></ul></body>'


def test_descendant_combinator_uses_the_full_matcher() -> None:
    document = parse("<main><section><article>in</article></section><article>out</article></main>")
    document.prune("section article")
    assert _body(document).serialize() == "<body><main><section><article>in</article></section></main></body>"


def test_nested_matches_keep_the_outer_whole_subtree() -> None:
    document = parse("<div class=keep><span class=keep>x</span><i>y</i></div><div>drop</div>")
    document.prune(".keep")
    assert _body(document).serialize() == '<body><div class="keep"><span class="keep">x</span><i>y</i></div></body>'


def test_grows_the_keep_buffer_for_many_matches() -> None:
    items = "".join(f"<li class=x>{index}</li>" for index in range(20))
    document = parse(f"<ul>{items}</ul>")
    container = document.select_one("ul")
    assert container is not None
    container.prune("li.x")
    assert len(container.select("li")) == 20


def test_no_match_empties_the_subtree() -> None:
    document = parse("<main><p>a</p><p>b</p></main>")
    main = document.select_one("main")
    assert main is not None
    main.prune("article")
    assert main.serialize() == "<main></main>"


def test_no_match_on_the_document_root_empties_everything() -> None:
    document = parse("<main><p>a</p></main>")
    document.prune("article")
    assert document.select_one("html") is None


def test_removes_text_and_comment_nodes_outside_a_match() -> None:
    document = parse("<main>loose<!--c--><article>keep</article>more</main>")
    document.prune("article")
    assert _body(document).serialize() == "<body><main><article>keep</article></main></body>"


def test_prunes_relative_to_the_node_it_is_called_on() -> None:
    document = parse("<section><h1>title</h1><div><p class=hit>x</p><p>y</p></div></section>")
    section = document.select_one("section")
    assert section is not None
    section.prune(".hit")
    assert section.serialize() == '<section><div><p class="hit">x</p></div></section>'


def test_returns_the_node_for_chaining() -> None:
    document = parse("<main><article>a</article></main>")
    assert document.prune("article") is document


def test_keeps_a_deep_match_through_its_ancestor_chain() -> None:
    markup = "<main><section><article class=keep>deep</article></section><aside>drop</aside></main>"
    document = parse(markup)
    document.prune(".keep")
    assert (
        _body(document).serialize()
        == '<body><main><section><article class="keep">deep</article></section></main></body>'
    )


def test_rejects_a_non_str_selector() -> None:
    document = parse("<p>x</p>")
    with pytest.raises(TypeError):
        getattr(document, "prune")(123)  # ruff:ignore[get-attr-with-constant]  # getattr keeps the bad arg from the type checker


def test_rejects_an_invalid_selector() -> None:
    with pytest.raises(ValueError, match="selector"):
        parse("<p>x</p>").prune("[")


@pytest.mark.parametrize(
    ("markup", "where", "selector", "expected"),
    [
        pytest.param(
            "<main><article>text<b>bold</b></article><p>keep</p></main>",
            None,
            "article",
            "<body><main><p>keep</p></main></body>",
            id="single-match-and-its-subtree",
        ),
        pytest.param(
            "<ul><li class=off>a</li><li>b</li><li class=off>c</li></ul>",
            None,
            "li.off",
            "<body><ul><li>b</li></ul></body>",
            id="each-of-several-matches",
        ),
        pytest.param(
            "<main>loose<!--c--><aside>drop</aside>more</main>",
            None,
            "aside",
            "<body><main>loose<!--c-->more</main></body>",
            id="keeps-unrelated-siblings-and-text",
        ),
        pytest.param(
            "<main><section><article>in</article></section><article>out</article></main>",
            None,
            "section article",
            "<body><main><section></section><article>out</article></main></body>",
            id="descendant-combinator-uses-the-full-matcher",
        ),
        pytest.param(
            "<div class=drop><span class=drop>x</span><i>y</i></div><p>stay</p>",
            None,
            ".drop",
            "<body><p>stay</p></body>",
            id="nested-match-inside-a-removed-match-drops-harmlessly",
        ),
        pytest.param(
            "<main><p>a</p><p>b</p></main>",
            "main",
            "article",
            "<main><p>a</p><p>b</p></main>",
            id="no-match-leaves-the-subtree-untouched",
        ),
    ],
)
def test_remove_drops_each_matching_subtree(markup: str, where: str | None, selector: str, expected: str) -> None:
    document = parse(markup)
    target = _body(document) if where is None else _node(document, where)
    target.remove(selector)
    assert target.serialize() == expected


@pytest.mark.parametrize(
    ("markup", "where", "selector", "expected"),
    [
        pytest.param(
            "<p>a <b>bold</b> z</p>",
            None,
            "b",
            "<body><p>a bold z</p></body>",
            id="single-match-keeping-its-children",
        ),
        pytest.param(
            "<p><span>one</span> and <span>two</span></p>",
            None,
            "span",
            "<body><p>one and two</p></body>",
            id="each-of-several-matches",
        ),
        pytest.param(
            "<div class=wrap><p>x</p><p>y</p></div>",
            None,
            ".wrap",
            "<body><p>x</p><p>y</p></body>",
            id="a-match-with-element-children",
        ),
        pytest.param(
            "<p>a<span></span>b</p>",
            None,
            "span",
            "<body><p>ab</p></body>",
            id="empty-match-is-just-dropped",
        ),
        pytest.param(
            "<p><b><i>x</i></b></p>",
            None,
            "b, i",
            "<body><p>x</p></body>",
            id="nested-matches-collapse-to-the-inner-content",
        ),
        pytest.param(
            "<main><section><b>in</b></section><b>out</b></main>",
            None,
            "section b",
            "<body><main><section>in</section><b>out</b></main></body>",
            id="descendant-combinator-uses-the-full-matcher",
        ),
        pytest.param(
            "<p>a<b>x</b></p>",
            "p",
            "i",
            "<p>a<b>x</b></p>",
            id="no-match-leaves-the-subtree-untouched",
        ),
    ],
)
def test_strip_tags_unwraps_each_matching_element(markup: str, where: str | None, selector: str, expected: str) -> None:
    document = parse(markup)
    target = _body(document) if where is None else _node(document, where)
    target.strip_tags(selector)
    assert target.serialize() == expected


@pytest.mark.parametrize(
    ("method", "markup", "where", "selector", "expected"),
    [
        pytest.param(
            "remove",
            "<section><p class=hit>x</p></section><p class=hit>y</p>",
            "section",
            ".hit",
            ("<section></section>", '<body><section></section><p class="hit">y</p></body>'),
            id="remove",
        ),
        pytest.param(
            "strip_tags",
            "<section><b>in</b></section><b>out</b>",
            "section",
            "b",
            ("<section>in</section>", "<body><section>in</section><b>out</b></body>"),
            id="strip_tags",
        ),
    ],
)
def test_bulk_edit_acts_only_within_the_node_it_is_called_on(
    method: str,
    markup: str,
    where: str,
    selector: str,
    expected: tuple[str, str],
) -> None:
    expected_node, expected_body = expected
    document = parse(markup)
    node = _node(document, where)
    getattr(node, method)(selector)
    assert node.serialize() == expected_node
    assert _body(document).serialize() == expected_body


def test_remove_grows_the_snapshot_buffer_for_many_matches() -> None:
    items = "".join(f"<li class=x>{index}</li>" for index in range(20))
    document = parse(f"<ul>{items}<li>keep</li></ul>")
    container = _node(document, "ul")
    container.remove("li.x")
    assert [item.text for item in container.select("li")] == ["keep"]


def test_strip_tags_grows_the_snapshot_buffer_for_many_matches() -> None:
    spans = "".join(f"<span>{index}</span>" for index in range(20))
    document = parse(f"<p>{spans}</p>")
    paragraph = _node(document, "p")
    paragraph.strip_tags("span")
    assert paragraph.select("span") == []
    assert paragraph.text == "".join(str(index) for index in range(20))


def test_remove_is_the_inverse_of_prune() -> None:
    markup = "<main><article>keep</article><aside>drop</aside></main>"
    pruned = parse(markup)
    pruned.prune("aside")
    removed = parse(markup)
    removed.remove("article")
    assert _body(pruned).serialize() == "<body><main><aside>drop</aside></main></body>"
    assert _body(removed).serialize() == "<body><main><aside>drop</aside></main></body>"


def test_strip_tags_is_the_bulk_form_of_unwrap() -> None:
    bulk = parse("<p><b>x</b></p>")
    bulk.strip_tags("b")
    single = parse("<p><b>x</b></p>")
    _node(single, "b").unwrap()
    assert _body(bulk).serialize() == "<body><p>x</p></body>"
    assert _body(single).serialize() == "<body><p>x</p></body>"


@pytest.mark.parametrize("method", [pytest.param("remove", id="remove"), pytest.param("strip_tags", id="strip_tags")])
def test_bulk_edit_returns_the_node_for_chaining(method: str) -> None:
    document = parse("<main><article><b>a</b></article></main>")
    assert getattr(document, method)("article") is document


@pytest.mark.parametrize("method", [pytest.param("remove", id="remove"), pytest.param("strip_tags", id="strip_tags")])
def test_bulk_edit_rejects_a_non_str_selector(method: str) -> None:
    document = parse("<p>x</p>")
    with pytest.raises(TypeError):
        getattr(document, method)(123)  # getattr keeps the bad arg from the type checker


@pytest.mark.parametrize("method", [pytest.param("remove", id="remove"), pytest.param("strip_tags", id="strip_tags")])
def test_bulk_edit_rejects_an_invalid_selector(method: str) -> None:
    with pytest.raises(ValueError, match="selector"):
        getattr(parse("<p>x</p>"), method)("[")
