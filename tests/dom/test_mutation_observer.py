"""Synchronous mutation observation: MutationObserver records over the Node/Element mutation API."""

from __future__ import annotations

import gc
from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Element, Node, Text, parse
from turbohtml.mutations import MutationObserver, MutationRecord

if TYPE_CHECKING:
    from collections.abc import Iterable


def _element(node: Node | None) -> Element:
    assert isinstance(node, Element)
    return node


def _tags(nodes: Iterable[Node]) -> list[str]:
    return [_element(node).tag for node in nodes]


def _div(markup: str = "<div id=a></div>") -> Element:
    return _element(parse(markup).find(id="a"))


def _first_child(markup: str) -> Node:
    return _element(parse(markup).find("div")).children[0]


def test_child_list_append_record_shape() -> None:
    div = _div("<div id=a><span></span></div>")
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    added = Element("b")
    div.append(added)
    (record,) = observer.take_records()
    assert isinstance(record, MutationRecord)
    assert record.type == "childList"
    assert record.target == div
    assert record.added_nodes == (added,)
    assert record.removed_nodes == ()
    assert _element(record.previous_sibling).tag == "span"
    assert record.next_sibling is None
    assert record.attribute_name is None
    assert record.old_value is None


def test_take_records_drains_the_queue() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    div.append(Element("b"))
    assert len(observer.take_records()) == 1
    assert observer.take_records() == []


def test_insert_before_reports_both_siblings() -> None:
    div = _div("<div id=a><span></span></div>")
    span = _element(div.find("span"))
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    span.insert_before(Element("b"))
    (record,) = observer.take_records()
    assert record.previous_sibling is None
    assert record.next_sibling == span


def test_insert_after_and_index_insert_record() -> None:
    div = _div("<div id=a><span></span></div>")
    span = _element(div.find("span"))
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    span.insert_after(Element("b"))
    div.insert(0, Element("i"))
    assert [_tags(record.added_nodes) for record in observer.take_records()] == [["b"], ["i"]]


def test_extend_records_one_per_child() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    div.extend([Element("b"), Element("i")])
    assert len(observer.take_records()) == 2


def test_replace_with_records_insertion_then_removal() -> None:
    div = _div("<div id=a><span></span></div>")
    span = _element(div.find("span"))
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    span.replace_with(Element("b"))
    records = observer.take_records()
    assert [_tags(record.added_nodes) for record in records] == [["b"], []]
    assert records[1].removed_nodes[0] == span


def test_clear_records_each_removal() -> None:
    div = _div("<div id=a><span></span><i></i></div>")
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    div.clear()
    assert [_tags(record.removed_nodes) for record in observer.take_records()] == [["span"], ["i"]]


@pytest.mark.parametrize("method", ["extract", "decompose"], ids=["extract", "decompose"])
def test_removal_methods_record(method: str) -> None:
    div = _div("<div id=a><span></span></div>")
    span = _element(div.find("span"))
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    getattr(span, method)()
    (record,) = observer.take_records()
    assert record.removed_nodes[0] == span
    assert record.added_nodes == ()


def test_set_text_records_removal_and_insertion() -> None:
    div = _div("<div id=a><span></span></div>")
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    div.set_text("hi")
    records = observer.take_records()
    assert _tags(records[0].removed_nodes) == ["span"]
    assert isinstance(records[1].added_nodes[0], Text)


def test_set_inner_html_records() -> None:
    div = _div("<div id=a><span></span></div>")
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    div.set_inner_html("<i></i>")
    records = observer.take_records()
    assert _tags(records[0].removed_nodes) == ["span"]
    assert _tags(records[-1].added_nodes) == ["i"]


def test_wrap_in_records() -> None:
    div = _div("<div id=a><span></span></div>")
    span = _element(div.find("span"))
    observer = MutationObserver()
    observer.observe(div, child_list=True, subtree=True)
    span.wrap(Element("b"))
    assert observer.take_records()  # wrap moves span under a new wrapper


def test_unwrap_records() -> None:
    div = _div("<div id=a><b><span></span></b></div>")
    wrapper = _element(div.find("b"))
    observer = MutationObserver()
    observer.observe(div, child_list=True, subtree=True)
    wrapper.unwrap()
    assert observer.take_records()


def test_move_records_removal_and_insertion() -> None:
    doc = parse("<div id=a><span></span></div><div id=b></div>")
    source = _element(doc.find(id="a"))
    dest = _element(doc.find(id="b"))
    span = _element(source.find("span"))
    removals = MutationObserver()
    removals.observe(source, child_list=True)
    insertions = MutationObserver()
    insertions.observe(dest, child_list=True)
    dest.append(span)
    (removal,) = removals.take_records()
    (insertion,) = insertions.take_records()
    assert removal.removed_nodes[0] == span
    assert insertion.added_nodes[0] == span


def test_attribute_added_reports_no_old_value() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, attributes=True, attribute_old_value=True)
    div.attrs["data-x"] = "1"
    (record,) = observer.take_records()
    assert record.type == "attributes"
    assert record.attribute_name == "data-x"
    assert record.old_value is None


def test_attribute_replaced_reports_old_value() -> None:
    div = _div('<div id=a data-x="1"></div>')
    observer = MutationObserver()
    observer.observe(div, attributes=True, attribute_old_value=True)
    div.attrs["data-x"] = "2"
    (record,) = observer.take_records()
    assert record.old_value == "1"


def test_attribute_without_old_value_flag_reports_none() -> None:
    div = _div('<div id=a data-x="1"></div>')
    observer = MutationObserver()
    observer.observe(div, attributes=True)
    div.attrs["data-x"] = "2"
    (record,) = observer.take_records()
    assert record.old_value is None


def test_valueless_attribute_old_value_is_empty() -> None:
    field = _element(parse("<input id=a disabled>").find(id="a"))
    observer = MutationObserver()
    observer.observe(field, attributes=True, attribute_old_value=True)
    field.attrs["disabled"] = "off"
    (record,) = observer.take_records()
    assert record.old_value is not None  # an empty string, distinct from None
    assert not record.old_value


def test_attribute_delete_records_old_value() -> None:
    div = _div('<div id=a data-x="gone"></div>')
    observer = MutationObserver()
    observer.observe(div, attributes=True, attribute_old_value=True)
    del div.attrs["data-x"]
    (record,) = observer.take_records()
    assert record.attribute_name == "data-x"
    assert record.old_value == "gone"


def test_attribute_filter_limits_names() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, attributes=True, attribute_filter=["data-x", "data-y"])
    div.attrs["data-x"] = "1"
    div.attrs["class"] = "ignored"
    div.attrs["data-y"] = "2"
    assert [record.attribute_name for record in observer.take_records()] == ["data-x", "data-y"]


def test_empty_attribute_filter_matches_nothing() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, attributes=True, attribute_filter=[])
    div.attrs["data-x"] = "1"
    assert observer.take_records() == []


def test_attribute_old_value_implies_attributes() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, attribute_old_value=True)
    div.attrs["data-x"] = "1"
    assert len(observer.take_records()) == 1


def test_attribute_filter_implies_attributes() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, attribute_filter=["data-x"])
    div.attrs["data-x"] = "1"
    assert len(observer.take_records()) == 1


def test_character_data_old_value_implies_character_data() -> None:
    text = _first_child("<div>hello</div>")
    assert isinstance(text, Text)
    observer = MutationObserver()
    observer.observe(text, character_data_old_value=True)
    text.data = "world"
    assert len(observer.take_records()) == 1


def test_character_data_records_old_value() -> None:
    text = _first_child("<div>hello</div>")
    assert isinstance(text, Text)
    observer = MutationObserver()
    observer.observe(text, character_data=True, character_data_old_value=True)
    text.data = "world"
    (record,) = observer.take_records()
    assert record.type == "characterData"
    assert record.old_value == "hello"


def test_character_data_without_flag_reports_none() -> None:
    text = _first_child("<div>hello</div>")
    assert isinstance(text, Text)
    observer = MutationObserver()
    observer.observe(text, character_data=True)
    text.data = "world"
    (record,) = observer.take_records()
    assert record.old_value is None


def test_character_data_empty_old_value() -> None:
    comment = _first_child("<div><!----></div>")
    assert isinstance(comment, Comment)
    observer = MutationObserver()
    observer.observe(comment, character_data=True, character_data_old_value=True)
    comment.data = "now"
    (record,) = observer.take_records()
    assert record.old_value is not None  # an empty string, distinct from None
    assert not record.old_value


def test_attributes_only_ignores_child_list() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, attributes=True)
    div.append(Element("b"))
    assert observer.take_records() == []


def test_child_list_only_ignores_attributes() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    div.attrs["data-x"] = "1"
    assert observer.take_records() == []


def test_child_list_only_ignores_character_data() -> None:
    text = _first_child("<div>hi</div>")
    assert isinstance(text, Text)
    observer = MutationObserver()
    observer.observe(text, child_list=True)
    text.data = "bye"
    assert observer.take_records() == []


def test_subtree_records_descendant_change() -> None:
    div = _div("<div id=a><span></span></div>")
    span = _element(div.find("span"))
    observer = MutationObserver()
    observer.observe(div, child_list=True, subtree=True)
    span.append(Element("i"))
    (record,) = observer.take_records()
    assert record.target == span


def test_without_subtree_ignores_descendant_change() -> None:
    div = _div("<div id=a><span></span></div>")
    span = _element(div.find("span"))
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    span.append(Element("i"))
    assert observer.take_records() == []


def test_subtree_records_descendant_attribute() -> None:
    div = _div("<div id=a><span></span></div>")
    span = _element(div.find("span"))
    observer = MutationObserver()
    observer.observe(div, attributes=True, subtree=True)
    span.attrs["data-x"] = "1"
    (record,) = observer.take_records()
    assert record.target == span


def test_multiple_observers_each_receive_records() -> None:
    div = _div()
    first = MutationObserver()
    second = MutationObserver()
    first.observe(div, child_list=True)
    second.observe(div, child_list=True)
    div.append(Element("b"))
    assert len(first.take_records()) == 1
    assert len(second.take_records()) == 1


def test_one_observer_two_targets() -> None:
    doc = parse("<div id=a></div><div id=b></div>")
    left = _element(doc.find(id="a"))
    right = _element(doc.find(id="b"))
    observer = MutationObserver()
    observer.observe(left, child_list=True)
    observer.observe(right, child_list=True)
    left.append(Element("b"))
    right.append(Element("i"))
    assert len(observer.take_records()) == 2


def test_reobserving_a_target_replaces_options() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, attributes=True, attribute_filter=["data-x"])
    observer.observe(div, child_list=True)  # replaces: now only childList
    div.attrs["data-x"] = "1"
    div.append(Element("b"))
    assert [record.type for record in observer.take_records()] == ["childList"]


def test_disconnect_clears_queue_and_stops() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    div.append(Element("b"))
    observer.disconnect()
    assert observer.take_records() == []
    div.append(Element("i"))
    assert observer.take_records() == []


def test_disconnect_without_observing_is_a_noop() -> None:
    observer = MutationObserver()
    observer.disconnect()  # never observed: nothing to tear down
    assert observer.take_records() == []


def test_reobserve_after_disconnect() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    observer.disconnect()
    observer.observe(div, child_list=True)
    div.append(Element("b"))
    assert len(observer.take_records()) == 1


def test_disconnect_one_of_several_observers() -> None:
    div = _div()
    first = MutationObserver()
    second = MutationObserver()
    first.observe(div, child_list=True)
    second.observe(div, child_list=True)
    first.disconnect()  # removes a non-last entry, leaving the registry non-empty
    div.append(Element("b"))
    assert first.take_records() == []
    assert len(second.take_records()) == 1
    second.disconnect()  # empties the registry


def test_deliver_calls_callback_with_records_and_observer() -> None:
    div = _div()
    seen: list[tuple[int, object]] = []
    observer = MutationObserver(lambda records, obs: seen.append((len(records), obs)))
    observer.observe(div, child_list=True)
    div.append(Element("b"))
    delivered = observer.deliver()
    assert len(delivered) == 1
    assert seen == [(1, observer)]
    assert observer.take_records() == []  # deliver drained the queue


def test_deliver_without_callback_returns_records() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    div.append(Element("b"))
    assert len(observer.deliver()) == 1


def test_deliver_with_empty_queue_skips_callback() -> None:
    div = _div()
    calls: list[int] = []
    observer = MutationObserver(lambda _records, _obs: calls.append(1))
    observer.observe(div, child_list=True)
    assert observer.deliver() == []
    assert calls == []


def test_deliver_propagates_callback_error() -> None:
    div = _div()

    def boom(_records: list[MutationRecord], _obs: MutationObserver) -> None:
        message = "callback failed"
        raise RuntimeError(message)

    observer = MutationObserver(boom)
    observer.observe(div, child_list=True)
    div.append(Element("b"))
    with pytest.raises(RuntimeError, match="callback failed"):
        observer.deliver()


def test_take_records_without_observing_is_empty() -> None:
    assert MutationObserver().take_records() == []


def test_deliver_without_observing_is_empty() -> None:
    calls: list[int] = []
    assert MutationObserver(lambda _records, _obs: calls.append(1)).deliver() == []
    assert calls == []


def test_repr() -> None:
    assert repr(MutationObserver()) == "MutationObserver()"


def test_many_records_grow_the_queue() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    for _ in range(20):
        div.append(Element("b"))
    assert len(observer.take_records()) == 20


def test_many_observers_and_targets() -> None:
    body = _element(parse("<body></body>").find("body"))
    targets = [Element(f"e{index}") for index in range(6)]
    for target in targets:
        body.append(target)
    observers = [MutationObserver() for _ in range(6)]
    for observer in observers:
        for target in targets:
            observer.observe(target, child_list=True)
    targets[0].append(Element("x"))
    assert sum(len(observer.take_records()) for observer in observers) == 6


def test_re_extract_detached_node_records_once() -> None:
    div = _div("<div id=a><span></span></div>")
    span = _element(div.find("span"))
    observer = MutationObserver()
    observer.observe(div, child_list=True)
    span.extract()
    span.extract()  # already detached: no second record
    assert len(observer.take_records()) == 1


def test_mutation_without_observers_still_works() -> None:
    div = _div("<div id=a><span>text</span></div>")
    span = _element(div.find("span"))
    text = span.children[0]
    assert isinstance(text, Text)
    div.append(Element("b"))
    div.attrs["data-x"] = "1"
    text.data = "changed"
    span.extract()
    assert div.find("b") is not None


def test_attribute_filter_none_is_no_filter() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, attributes=True, attribute_filter=None)
    div.attrs["data-x"] = "1"
    assert len(observer.take_records()) == 1


def test_attribute_filter_accepts_a_tuple() -> None:
    div = _div()
    observer = MutationObserver()
    observer.observe(div, attributes=True, attribute_filter=("data-x",))
    div.attrs["data-x"] = "1"
    div.attrs["data-y"] = "2"
    assert [record.attribute_name for record in observer.take_records()] == ["data-x"]


def test_lone_surrogate_filter_name_is_rejected() -> None:
    div = _div()
    with pytest.raises(UnicodeEncodeError):
        MutationObserver().observe(div, attributes=True, attribute_filter=[chr(0xD800)])


def test_observer_is_garbage_collected() -> None:
    div = _div()
    observed = MutationObserver()
    observed.observe(div, child_list=True)
    unobserved = MutationObserver(lambda _records, _obs: None)
    gc.collect()  # traverses both a bound and an unbound observer
    del observed
    del unobserved
    gc.collect()
    div.append(Element("b"))  # the freed observers left no dangling registration
    assert div.find("b") is not None


def test_target_must_be_a_node() -> None:
    with pytest.raises(TypeError):
        MutationObserver().observe("not a node", child_list=True)  # ty: ignore[invalid-argument-type]


def test_observe_requires_an_option() -> None:
    with pytest.raises(TypeError):
        MutationObserver().observe(_div())


def test_cross_tree_target_is_rejected() -> None:
    left = _div()
    right = _element(parse("<div id=b></div>").find(id="b"))
    observer = MutationObserver()
    observer.observe(left, child_list=True)
    with pytest.raises(ValueError, match="different tree"):
        observer.observe(right, child_list=True)


def test_non_callable_callback_is_rejected() -> None:
    with pytest.raises(TypeError, match="callable"):
        MutationObserver(42)  # ty: ignore[invalid-argument-type]


def test_too_many_constructor_arguments() -> None:
    with pytest.raises(TypeError):
        MutationObserver(None, None)  # ty: ignore[too-many-positional-arguments]


def test_observe_rejects_unknown_keyword() -> None:
    with pytest.raises(TypeError):
        MutationObserver().observe(_div(), child_list=True, bogus=1)  # ty: ignore[unknown-argument]


@pytest.mark.parametrize(
    "attribute_filter",
    [pytest.param([1], id="non-str-entry"), pytest.param(["ok", 1], id="non-str-after-valid")],
)
def test_non_str_attribute_filter_is_rejected(attribute_filter: list[object]) -> None:
    with pytest.raises(TypeError):
        MutationObserver().observe(_div(), attributes=True, attribute_filter=attribute_filter)  # ty: ignore[invalid-argument-type]


def test_non_iterable_attribute_filter_is_rejected() -> None:
    with pytest.raises(TypeError):
        MutationObserver().observe(_div(), attributes=True, attribute_filter=5)  # ty: ignore[invalid-argument-type]
