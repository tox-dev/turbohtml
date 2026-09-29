from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import cast

import pytest

from turbohtml import parse


@pytest.mark.parametrize("threaded", [pytest.param(False, id="same-thread"), pytest.param(True, id="other-thread")])
@pytest.mark.parametrize("checked", [pytest.param(False, id="uncheck"), pytest.param(True, id="check")])
@pytest.mark.parametrize("kind", [pytest.param("radio", id="radio"), pytest.param("checkbox", id="checkbox")])
def test_checked_truth_callback_adopts_input(kind: str, *, checked: bool, threaded: bool) -> None:
    source = parse(f'<form><input type="{kind}" name="group" {"" if checked else "checked"}></form>')
    destination = parse(f'<form><input type="{kind}" name="group" checked></form>')
    field = source.select("input")[0]
    form = destination.select("form")[0]

    class AdoptingTruth:
        def __bool__(self) -> bool:
            if threaded:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(form.append, field).result(timeout=5)
            else:
                form.append(field)
            return checked

    field.checked = cast("bool", AdoptingTruth())
    assert [item.checked for item in destination.select("input")] == [kind != "radio" or not checked, checked]


@pytest.mark.parametrize("checked", [pytest.param(False, id="uncheck"), pytest.param(True, id="check")])
@pytest.mark.parametrize("mutation", [pytest.param("tag", id="tag"), pytest.param("type", id="type")])
def test_checked_truth_callback_invalidates_input(mutation: str, *, checked: bool) -> None:
    document = parse("<input type=radio>")
    field = document.select("input")[0]

    class MutatingTruth:
        def __bool__(self) -> bool:
            if mutation == "tag":
                field.tag = "div"
            else:
                field.attrs["type"] = "text"
            return checked

    with pytest.raises(TypeError, match="checked can only be set on a checkbox or radio input"):
        field.checked = cast("bool", MutatingTruth())
