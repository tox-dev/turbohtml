from __future__ import annotations

from typing import TYPE_CHECKING, Final
from weakref import finalize

import pytest

from turbohtml import Element, parse

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize("all_matches", [pytest.param(False, id="find"), pytest.param(True, id="find-all")])
@pytest.mark.parametrize(
    "text",
    [
        pytest.param(None, id="attributes"),
        pytest.param("keep", id="literal-text"),
        pytest.param(lambda value: value == "keep", id="callable-text"),
    ],
)
def test_attribute_filters_survive_mapping_changes(
    text: str | Callable[[str | None], bool] | None, *, all_matches: bool
) -> None:
    document: Final = parse('<p data-a="a" data-b="b">keep</p><p data-a="c" data-b="d">keep</p>')
    calls: Final[list[str | None]] = []
    attrs: Final[dict[str, Callable[[str | None], bool]]] = {}

    def first(value: str | None) -> bool:
        calls.append(value)
        attrs.clear()
        return True

    attrs.update({"data-a": first, "data-b": lambda value: calls.append(value) or True})
    finalize(attrs["data-b"], calls.append, "released")
    if all_matches:
        matches = (
            document.find_all("p", attrs=attrs, text=text) if text is not None else document.find_all("p", attrs=attrs)
        )
    else:
        found: Final = (
            document.find("p", attrs=attrs, text=text) if text is not None else document.find("p", attrs=attrs)
        )
        assert found is not None
        matches = [found]
    assert ([node.text for node in matches], calls) == (
        ["keep"] * (2 if all_matches else 1),
        (["a", "b", "c", "d"] if all_matches or callable(text) else ["a", "b"]) + ["released"],
    )


@pytest.mark.parametrize("all_matches", [pytest.param(False, id="find"), pytest.param(True, id="find-all")])
def test_filter_cleanup_preserves_result_storage(*, all_matches: bool) -> None:
    root: Final = parse('<main><p data-x="yes">original</p></main>').find("main")
    assert root is not None
    target: Final = Element("article")
    attrs: Final[dict[str, Callable[[str | None], bool]]] = {}
    attrs["data-x"] = lambda _: attrs.clear() or True
    finalize(attrs["data-x"], target.append, root)
    if all_matches:
        matches = root.find_all("p", attrs=attrs)
    else:
        found: Final = root.find("p", attrs=attrs)
        assert found is not None
        matches = [found]
    assert ([node.text for node in matches], target.text) == (["original"], "original")


def test_attribute_filter_snapshot_releases_on_error() -> None:
    released: Final[list[str]] = []
    attrs: Final[dict[str, Callable[[str | None], bool]]] = {}

    def fail(_: str | None) -> bool:
        attrs.clear()
        raise LookupError

    attrs.update({"data-a": fail, "data-b": lambda _: True})
    finalize(attrs["data-b"], released.append, "released")
    with pytest.raises(LookupError):
        parse('<p data-a="a" data-b="b"></p>').find_all("p", attrs=attrs)
    assert released == ["released"]
