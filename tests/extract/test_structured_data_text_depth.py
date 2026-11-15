from __future__ import annotations

import re
from typing import Final

import pytest

from turbohtml import MicrodataItem, RdfaItem, parse, parse_xml

_LIMIT: Final[int] = 32
_TAG: Final[re.Pattern[str]] = re.compile(r"<(\w+)")


def _nest(opening: str, depth: int, inner: str = "x") -> str:
    closing: Final = "".join(f"</{name}>" for name in reversed(_TAG.findall(opening)))
    return opening * depth + inner + closing * depth


def test_microdata_text_properties_nest_up_to_the_limit() -> None:
    html: Final = f"<div itemscope>{_nest('<div itemprop=p>', _LIMIT)}</div>"
    assert parse(html).microdata()[0].properties == {"p": ["x"] * _LIMIT}


@pytest.mark.parametrize(
    ("html", "value"),
    [
        pytest.param(f"<div itemscope>{_nest('<div itemprop=p>', _LIMIT + 8)}", "x", id="one-item"),
        pytest.param(f"<div itemscope>{_nest('<div itemprop=p>', _LIMIT + 8, '')}", "", id="empty-innermost"),
        pytest.param(f"<div itemscope>{_nest('<time itemprop=p>', _LIMIT + 8)}", "x", id="time-without-datetime"),
        pytest.param(
            f"<div itemscope>{_nest('<time itemprop=p datetime>', _LIMIT + 8)}", "x", id="time-empty-datetime"
        ),
        pytest.param(
            f"<div itemscope><div itemprop=p>{'<div itemprop=q>y</div>' * 3}{_nest('<div itemprop=p>', _LIMIT + 7)}",
            "x",
            id="after-shallow-siblings",
        ),
    ],
)
def test_microdata_leaves_out_text_properties_that_nest_past_the_limit(html: str, value: str) -> None:
    # the eight outermost head a chain deeper than the limit; the rest keep their exact text content
    document: Final = parse(html)
    assert document.microdata()[0].properties["p"] == [value] * _LIMIT
    assert document.structured_data().microdata[0].properties["p"] == [value] * _LIMIT


def test_microdata_text_depth_ignores_item_boundaries() -> None:
    # every property belongs to an item of its own, yet the outermost one would still copy the text of all below it
    items: Final = parse(_nest("<div itemscope><div itemprop=p>", _LIMIT + 1)).microdata()
    assert [item.properties for item in items] == [{}, *([{"p": ["x"]}] * _LIMIT)]


def test_microdata_text_depth_ignores_nested_items() -> None:
    html: Final = f"<div itemscope>{_nest('<div itemprop=i itemscope><div itemprop=p>', _LIMIT + 1)}"
    item: MicrodataItem | str | None = parse(html).microdata()[0]
    texts: Final[list[list[str | MicrodataItem] | None]] = []
    while isinstance(item, MicrodataItem):
        texts.append(item.properties.get("p"))
        item = item.get("i")
    # the outer item has no text property, the first nested one loses its own to the chain below it
    assert texts == [None, None, *([["x"]] * _LIMIT)]


def test_microdata_text_depth_ignores_itemref() -> None:
    # each item claims the chain from one element down, innermost first; only the last one starts above the limit
    refs: Final = "".join(f"<div itemscope itemref=e{index}></div>" for index in reversed(range(_LIMIT + 1)))
    targets: Final = "".join(f"<div id=e{index} itemprop=p>" for index in range(_LIMIT + 1))
    records: Final = parse(f"{refs}{targets}x").microdata()
    assert [len(record.properties["p"]) for record in records] == [*range(1, _LIMIT + 1), _LIMIT]


def test_microdata_text_depth_reaches_into_a_template() -> None:
    html: Final = f"<div itemscope><div itemprop=p><template>{_nest('<div itemscope><div itemprop=p>', _LIMIT)}"
    assert parse(html).microdata()[0].properties == {}


@pytest.mark.parametrize(
    "wrapper",
    [
        pytest.param("<div>", id="no-property"),
        pytest.param("<div itemprop>", id="empty-itemprop"),
        pytest.param("<div itemprop=i itemscope>", id="item"),
        pytest.param("<data itemprop=d value=v>", id="attribute-valued"),
        pytest.param("<time itemprop=t datetime=2020>", id="time-with-datetime"),
    ],
)
def test_microdata_elements_without_a_text_value_do_not_count(wrapper: str) -> None:
    inner: Final = _nest(wrapper, 40, "<b itemprop=last>x</b>")
    html: Final = f"<div itemscope>{_nest('<div itemprop=p>', _LIMIT - 1, inner)}"
    assert parse(html).microdata()[0].properties["p"] == ["x"] * (_LIMIT - 1)


def test_microdata_text_depth_is_counted_along_one_branch() -> None:
    # three full chains side by side, then one that closes ten levels and reopens them: none is deeper than the limit
    side_by_side: Final = _nest("<div itemprop=p>", _LIMIT) * 3
    opened: Final = f"{'<div itemprop=p>' * _LIMIT}x{'</div>' * 10}"
    reopened: Final = f"{opened}<template>t</template>{_nest('<div itemprop=p>', 10)}"
    properties: Final = parse(f"<div itemscope>{side_by_side}{reopened}</div>").microdata()[0].properties
    assert len(properties["p"]) == _LIMIT * 4 + 10
    assert properties["p"][-10:] == ["x"] * 10
    # reopening eleven makes the outermost one head a chain of one more than the limit
    deeper: Final = parse(f"<div itemscope>{opened}{_nest('<div itemprop=p>', 11)}</div>").microdata()[0].properties
    assert len(deeper["p"]) == _LIMIT + 10


def test_rdfa_text_properties_nest_up_to_the_limit() -> None:
    html: Final = f"<div typeof=T>{_nest('<div property=p>', _LIMIT)}</div>"
    assert parse(html).rdfa()[0].properties == {"p": ["x"] * _LIMIT}


@pytest.mark.parametrize(
    "html",
    [
        pytest.param(f"<div typeof=T>{_nest('<div property=p>', _LIMIT + 8)}", id="one-item"),
        pytest.param(f"<div typeof=T>{_nest('<time property=p>', _LIMIT + 8)}", id="time-without-datetime"),
        pytest.param(f"<div typeof=T>{_nest('<time property=p datetime>', _LIMIT + 8)}", id="time-empty-datetime"),
        pytest.param(f"<div typeof=T>{_nest('<div property=p resource=[no:prefix]>', _LIMIT + 8)}", id="no-resource"),
    ],
)
def test_rdfa_leaves_out_text_properties_that_nest_past_the_limit(html: str) -> None:
    document: Final = parse(html)
    assert document.rdfa()[0].properties == {"p": ["x"] * _LIMIT}
    assert document.structured_data().rdfa[0].properties == {"p": ["x"] * _LIMIT}


def test_rdfa_text_depth_ignores_nested_items() -> None:
    html: Final = f"<div typeof=T>{_nest('<div property=i typeof=T><div property=p>', _LIMIT + 1)}"
    item: RdfaItem | str | None = parse(html).rdfa()[0]
    texts: Final[list[list[str | RdfaItem] | None]] = []
    while isinstance(item, RdfaItem):
        texts.append(item.properties.get("p"))
        item = item.get("i")
    assert texts == [None, None, *([["x"]] * _LIMIT)]


@pytest.mark.parametrize(
    "wrapper",
    [
        pytest.param("<div>", id="no-property"),
        pytest.param("<div property>", id="empty-property"),
        pytest.param("<div property=c content=v>", id="content"),
        pytest.param("<div property=i typeof=T>", id="item"),
        pytest.param("<div property=h href=u>", id="href"),
        pytest.param("<div property=s src=u>", id="src"),
        pytest.param("<time property=t datetime=2020>", id="time-with-datetime"),
    ],
)
def test_rdfa_elements_without_a_text_value_do_not_count(wrapper: str) -> None:
    inner: Final = _nest(wrapper, 40, "<b property=last>x</b>")
    html: Final = f"<div typeof=T>{_nest('<div property=p>', _LIMIT - 1, inner)}"
    assert parse(html).rdfa()[0].properties["p"] == ["x"] * (_LIMIT - 1)


@pytest.mark.parametrize(
    ("page", "extract"),
    [
        pytest.param('<r itemscope="">{}</r>', "microdata", id="microdata"),
        pytest.param('<r typeof="T">{}</r>', "rdfa", id="rdfa"),
    ],
)
def test_a_chain_of_text_properties_as_long_as_the_page_stays_bounded(page: str, extract: str) -> None:
    # parse_xml() has no depth cap, so the limit alone bounds how often the text below each property is copied
    chain: Final = _nest('<a itemprop="p" property="p">', 2_000, "x" * 10_000)
    values: Final = getattr(parse_xml(page.format(chain)), extract)()[0].properties["p"]
    assert values == ["x" * 10_000] * _LIMIT
