from __future__ import annotations

import pytest

from turbohtml._html import microdata_as_dict as _microdata_as_dict


class _WrongProperties:
    properties = "not a mapping"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("notanitem", id="a-string"),
        pytest.param(5, id="a-number"),
        pytest.param(None, id="none"),
        pytest.param(_WrongProperties(), id="properties-is-not-a-mapping"),
    ],
)
def test_the_binding_rejects_what_is_not_an_item(value: str | int | _WrongProperties | None) -> None:
    with pytest.raises(TypeError, match="expected a MicrodataItem"):
        _microdata_as_dict(value)  # ty: ignore[invalid-argument-type]  # tests the C argument guard
