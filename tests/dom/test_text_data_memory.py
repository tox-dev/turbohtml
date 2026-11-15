from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final, TypeAlias

import pytest

from turbohtml import Comment, Element, Range, Text, parse
from turbohtml.clean import Policy, collapse_whitespace_node, sanitize
from turbohtml.etree import fragment_fromstring, tostring

if TYPE_CHECKING:
    from collections.abc import Callable

    from traced_bytes import RetainedBytes
    from work_steps import WorkSteps

    Run: TypeAlias = tuple[Callable[[], None], Callable[[], str]]


def _chunk(index: int) -> str:
    return f"[{index}]"


def _texts(markup: str) -> list[Text]:
    paragraph: Final = parse(markup).find("p")
    assert paragraph is not None
    texts: Final = [child for child in paragraph.children if isinstance(child, Text)]
    assert texts
    return texts


def _append(count: int) -> Run:
    text: Final = _texts("<p>s</p>")[0]

    def run() -> None:
        for index in range(count):
            text.data += _chunk(index)

    return run, lambda: text.data


def _prepend(count: int) -> Run:
    text: Final = _texts("<p>s</p>")[0]

    def run() -> None:
        for index in range(count):
            text.data = _chunk(index) + text.data

    return run, lambda: text.data


def _insert_middle(count: int) -> Run:
    text: Final = _texts("<p>s</p>")[0]

    def run() -> None:
        for index in range(count):
            data = text.data
            text.data = data[: len(data) // 2] + _chunk(index) + data[len(data) // 2 :]

    return run, lambda: text.data


def _clear_and_refill(count: int) -> Run:
    text: Final = _texts("<p>s</p>")[0]

    def run() -> None:
        for index in range(count):
            data = text.data
            text.data = ""
            text.data = data + _chunk(index)

    return run, lambda: text.data


def _comment_append(count: int) -> Run:
    paragraph: Final = parse("<p><!--s--></p>").find("p")
    assert paragraph is not None
    comment: Final = paragraph.children[0]
    assert isinstance(comment, Comment)

    def run() -> None:
        for index in range(count):
            comment.data += _chunk(index)

    return run, lambda: comment.data


def _rotation(count: int) -> Run:
    texts: Final = _texts(f"<p>{'<br>'.join('abcdefgh')}</p>")

    def run() -> None:
        for index in range(count):
            texts[index % len(texts)].data += _chunk(index)

    return run, lambda: "|".join(text.data for text in texts)


def _normalize(count: int) -> Run:
    paragraph: Final = parse("<p>s</p>").find("p")
    assert paragraph is not None

    def run() -> None:
        for index in range(count):
            paragraph.append(Text(_chunk(index)))
            paragraph.normalize()

    return run, lambda: "|".join(child.data for child in paragraph.children if isinstance(child, Text))


def _etree_tail_append(count: int) -> Run:
    tree: Final = fragment_fromstring("<div><hr>s</div>")

    def run() -> None:
        for index in range(count):
            tree[0].tail = (tree[0].tail or "") + _chunk(index)

    return run, lambda: tostring(tree, encoding="unicode")


def _etree_text_prepend(count: int) -> Run:
    tree: Final = fragment_fromstring("<div>s</div>")

    def run() -> None:
        for index in range(count):
            tree.text = _chunk(index) + (tree.text or "")

    return run, lambda: tostring(tree, encoding="unicode")


def _etree_tail_rotation(count: int) -> Run:
    tree: Final = fragment_fromstring(f"<div>{''.join(f'<hr>{letter}' for letter in 'abcdefgh')}</div>")

    def run() -> None:
        for index in range(count):
            element = tree[index % len(tree)]
            element.tail = (element.tail or "") + _chunk(index)

    return run, lambda: tostring(tree, encoding="unicode")


def _etree_drop_keeping_tail(count: int) -> Run:
    tree: Final = fragment_fromstring(f"<div>{''.join(f'<em></em>{_chunk(index)}' for index in range(count))}</div>")

    def run() -> None:
        for element in tree.findall("em"):  # each removed element hands its tail to the text before it, as in lxml
            tree.text = (tree.text or "") + (element.tail or "")
            tree.remove(element)

    return run, lambda: tostring(tree, encoding="unicode")


_SHAPES: Final[MappingProxyType[str, Callable[[int], Run]]] = MappingProxyType({
    "append": _append,
    "prepend": _prepend,
    "insert-middle": _insert_middle,
    "clear-and-refill": _clear_and_refill,
    "comment-append": _comment_append,
    "rotation": _rotation,
    "normalize": _normalize,
    "etree-tail-append": _etree_tail_append,
    "etree-text-prepend": _etree_text_prepend,
    "etree-tail-rotation": _etree_tail_rotation,
    "etree-drop-keeping-tail": _etree_drop_keeping_tail,
})


@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        pytest.param("append", "s[0][1][2]", id="append"),
        pytest.param("prepend", "[2][1][0]s", id="prepend"),
        pytest.param("insert-middle", "[0[[2]1]]s", id="insert-middle"),
        pytest.param("clear-and-refill", "s[0][1][2]", id="clear-and-refill"),
        pytest.param("comment-append", "s[0][1][2]", id="comment-append"),
        pytest.param("rotation", "a[0]|b[1]|c[2]|d|e|f|g|h", id="rotation"),
        pytest.param("normalize", "s[0][1][2]", id="normalize"),
        pytest.param("etree-tail-append", "<div><hr/>s[0][1][2]</div>", id="etree-tail-append"),
        pytest.param("etree-text-prepend", "<div>[2][1][0]s</div>", id="etree-text-prepend"),
        pytest.param(
            "etree-tail-rotation",
            "<div><hr/>a[0]<hr/>b[1]<hr/>c[2]<hr/>d<hr/>e<hr/>f<hr/>g<hr/>h</div>",
            id="etree-tail-rotation",
        ),
        pytest.param("etree-drop-keeping-tail", "<div>[0][1][2]</div>", id="etree-drop-keeping-tail"),
    ],
)
def test_repeated_text_writes_store_each_value(shape: str, expected: str) -> None:
    run, result = _SHAPES[shape](3)
    run()
    assert result() == expected


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_repeated_text_writes_allocate_linearly(work_steps: WorkSteps, shape: str) -> None:
    work_steps.assert_linear(lambda count: _SHAPES[shape](count)[0], (300, 1_200))


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_repeated_text_writes_retain_memory_of_the_final_text(retained_bytes: RetainedBytes, shape: str) -> None:
    count: Final = 2_000
    run, _ = _SHAPES[shape](count)
    before: Final = retained_bytes(__file__)
    run()
    retained: Final = retained_bytes(__file__) - before
    # the nodes an edit creates and a buffer at most twice the final text stay in the arena, a few hundred bytes per
    # write; a copy kept per write grows with the square of the count instead
    assert retained < 512 * count


def _delete(text: Text, start: int, end: int) -> None:
    boundary: Final = Range(text, start)
    boundary.set_end(text, end)
    boundary.delete_contents()


@pytest.mark.parametrize(
    ("edit", "more", "expected"),
    [
        pytest.param(lambda text: _delete(text, 0, 4), "mnopqrst", ["efghijklmnopqrst"], id="drop-front"),
        pytest.param(lambda text: _delete(text, 4, 6), "mnopqr", ["abcdghijklmnopqr"], id="drop-middle"),
        pytest.param(lambda text: _delete(text, 0, 12), "mnopqrstuvwxyz01", ["mnopqrstuvwxyz01"], id="drop-all"),
        pytest.param(
            lambda text: Range(text, 4).insert_node(Element("b")),
            "mnopqrstuvwx",
            ["abcdmnopqrstuvwx", "efghijkl"],
            id="split",
        ),
    ],
)
def test_range_edited_text_grows_without_overrunning_its_buffer(
    edit: Callable[[Text], None], more: str, expected: list[str]
) -> None:
    paragraph: Final = parse("<p>abcdefgh<br>one<br>two</p>").find("p")
    assert paragraph is not None
    text, before, after = (child for child in paragraph.children if isinstance(child, Text))
    before.data, after.data = "x", "y"  # their parsed text is copied out first, so the writes below allocate only once
    text.data += "ij"
    text.data += "kl"  # a buffer with room for 16
    before.data = "next!"  # allocated right behind the buffer
    edit(text)
    after.data = "last!"  # allocated right behind whatever the edit allocated
    text.data += more  # sixteen code points again
    assert [child.data for child in paragraph.children if isinstance(child, Text)] == [*expected, "next!", "last!"]


def test_selectedcontent_keeps_its_text_when_the_option_text_is_rewritten() -> None:
    # the ignored end tag splits the option text, which the parser merges into a buffer with room
    document: Final = parse("<select><button><selectedcontent></selectedcontent></button><option>ab</x>cd</select>")
    option: Final = document.find("option")
    assert option is not None
    text: Final = option.children[0]
    assert isinstance(text, Text)
    text.data = "wxyz"
    selected: Final = document.find("selectedcontent")
    assert selected is not None
    assert (selected.html, option.html) == ("<selectedcontent>abcd</selectedcontent>", "<option>wxyz</option>")


def test_collapsed_text_grows_again_without_overrunning_its_buffer() -> None:
    paragraph: Final = parse("<p>a<br>tail</p>").find("p")
    assert paragraph is not None
    texts: Final = [child for child in paragraph.children if isinstance(child, Text)]
    texts[0].data += "b" * 6
    texts[0].data += "c" * 6  # a buffer with room for 16
    texts[0].data = "x   y"
    collapse_whitespace_node(paragraph)  # stores "x y" in a buffer of its own size
    texts[1].data = "next"  # allocated right behind it
    texts[0].data += "z" * 10
    assert (texts[0].data, texts[1].data) == ("x yzzzzzzzzzz", "next")


def test_renamed_element_keeps_the_name_its_clone_shares() -> None:
    policy: Final = Policy(tags=frozenset({"i", "p"}), transform_tags={"b": "i"})
    assert sanitize("<b>1<p>2</b>3</p>", policy) == "<i>1</i><p><i>2</i>3</p>"
