"""Reading and writing Element.field_value across form-control types."""

from __future__ import annotations

from typing import Any

import pytest

from turbohtml import Element, parse


def _control(markup: str, selector: str) -> Element:
    element = parse(markup).find(selector)
    assert element is not None
    return element


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<input name=q value=hello>", "hello", id="text-value"),
        pytest.param("<input name=q>", "", id="text-missing-value"),
        pytest.param("<input type=hidden value=tok>", "tok", id="hidden"),
        pytest.param("<input type=checkbox>", "on", id="checkbox-default-on"),
        pytest.param("<input type=checkbox value=yes>", "yes", id="checkbox-explicit"),
        pytest.param("<input type=radio>", "on", id="radio-default-on"),
        pytest.param("<input type=Checkbox value=x>", "x", id="checkbox-case-insensitive"),
        pytest.param("<input type=checkbox value>", "", id="checkbox-empty-valueless"),
    ],
)
def test_input_field_value(markup: str, expected: str) -> None:
    assert _control(markup, "input").field_value == expected


def test_textarea_field_value_is_its_text() -> None:
    assert _control("<textarea> hi there </textarea>", "textarea").field_value == " hi there "


def test_button_field_value() -> None:
    assert _control("<button value=go>Go</button>", "button").field_value == "go"


def test_button_field_value_defaults_empty() -> None:
    assert _control("<button>Go</button>", "button").field_value == ""  # ruff:ignore[compare-to-empty-string]


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<option value=r>Red</option>", "r", id="value-attribute"),
        pytest.param("<option> Red </option>", "Red", id="text-stripped"),
        pytest.param("<option value>Red</option>", "", id="empty-valueless"),
    ],
)
def test_option_field_value(markup: str, expected: str) -> None:
    assert _control(markup, "option").field_value == expected


def test_single_select_returns_the_selected_option() -> None:
    select = _control("<select><option value=r>Red<option value=g selected>Green</select>", "select")
    assert select.field_value == "g"


def test_single_select_defaults_to_the_first_option() -> None:
    select = _control("<select><option value=r>Red<option value=g>Green</select>", "select")
    assert select.field_value == "r"


def test_single_select_last_selected_wins() -> None:
    select = _control("<select><option value=r selected>Red<option value=g selected>Green</select>", "select")
    assert select.field_value == "g"


def test_empty_single_select_is_none() -> None:
    assert _control("<select></select>", "select").field_value is None


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        pytest.param("1", "a", id="display-size-1-defaults-to-first"),
        pytest.param("4", None, id="display-size-4-has-no-default"),
    ],
)
def test_single_select_default_only_when_display_size_is_one(size: str, expected: str | None) -> None:
    select = _control(f"<select size={size}><option value=a><option value=b></select>", "select")
    assert select.field_value == expected


def test_single_select_default_skips_an_optgroup_disabled_option() -> None:
    select = _control("<select><optgroup disabled><option value=a>A</optgroup><option value=b>B</select>", "select")
    assert select.field_value == "b"


def test_single_select_keeps_a_disabled_selected_option() -> None:
    select = _control("<select><option value=a disabled selected><option value=b></select>", "select")
    assert select.field_value == "a"


def test_multiple_select_returns_the_selected_list() -> None:
    select = _control(
        "<select multiple><option value=a selected>A<option value=b><option value=c selected>C</select>", "select"
    )
    assert select.field_value == ["a", "c"]


def test_multiple_select_with_no_selection_is_empty() -> None:
    select = _control("<select multiple><option value=a>A<option value=b>B</select>", "select")
    assert select.field_value == []


def test_non_control_field_value_is_none() -> None:
    assert _control("<div>hi</div>", "div").field_value is None


def test_set_input_value_writes_the_attribute() -> None:
    field = _control("<input name=q value=old>", "input")
    field.field_value = "new"
    assert field.field_value == "new"
    assert field.html == '<input name="q" value="new">'


def test_set_input_value_to_none_removes_the_attribute() -> None:
    field = _control("<input name=q value=old>", "input")
    field.field_value = None
    assert field.html == '<input name="q">'


def test_delete_input_value_removes_the_attribute() -> None:
    field: Any = _control("<input name=q value=old>", "input")
    del field.field_value
    assert field.html == '<input name="q">'


def test_set_input_value_rejects_non_str() -> None:
    field: Any = _control("<input name=q>", "input")
    with pytest.raises(TypeError, match="must be a str or None"):
        field.field_value = 5


def test_set_textarea_value_replaces_text() -> None:
    field = _control("<textarea>old</textarea>", "textarea")
    field.field_value = "new"
    assert field.field_value == "new"
    assert field.html == "<textarea>new</textarea>"


def test_set_textarea_value_to_none_clears_it() -> None:
    field = _control("<textarea>old</textarea>", "textarea")
    field.field_value = None
    assert field.field_value == ""  # ruff:ignore[compare-to-empty-string]
    assert field.html == "<textarea></textarea>"


def test_set_textarea_value_rejects_a_list() -> None:
    field = _control("<textarea></textarea>", "textarea")
    with pytest.raises(TypeError, match="must be a str or None"):
        field.field_value = ["x"]


def test_set_single_select_selects_the_match() -> None:
    select = _control("<select><option value=r selected>Red<option value=g>Green</select>", "select")
    select.field_value = "g"
    assert select.field_value == "g"
    assert select.html == '<select><option value="r">Red</option><option value="g" selected="">Green</option></select>'


def test_set_single_select_only_selects_the_first_match() -> None:
    select = _control("<select><option value=x>One<option value=x>Two</select>", "select")
    select.field_value = "x"
    options = select.find_all("option")
    assert [option.checked for option in options] == [False, False]
    assert "selected" in options[0].attrs
    assert "selected" not in options[1].attrs


def test_set_single_select_to_none_clears_all() -> None:
    select = _control("<select><option value=r selected>Red<option value=g>Green</select>", "select")
    select.field_value = None
    assert "selected" not in select.find_all("option")[0].attrs


def test_set_single_select_rejects_a_list() -> None:
    select = _control("<select><option value=r>Red</select>", "select")
    with pytest.raises(TypeError, match="single select must be a str or None"):
        select.field_value = ["r"]


def test_set_multiple_select_selects_each_match() -> None:
    select = _control("<select multiple><option value=a>A<option value=b>B<option value=c>C</select>", "select")
    select.field_value = ["a", "c"]
    assert select.field_value == ["a", "c"]


def test_set_multiple_select_deselects_the_rest() -> None:
    select = _control("<select multiple><option value=a selected>A<option value=b selected>B</select>", "select")
    select.field_value = ["a"]
    assert select.field_value == ["a"]


def test_set_multiple_select_rejects_a_bare_str() -> None:
    select = _control("<select multiple><option value=a>A</select>", "select")
    with pytest.raises(TypeError, match="multiple select must be a list of str or None"):
        select.field_value = "a"


def test_set_multiple_select_rejects_non_str_member() -> None:
    select: Any = _control("<select multiple><option value=a>A</select>", "select")
    with pytest.raises(TypeError, match="multiple select must be a list of str or None"):
        select.field_value = [1]


def test_set_field_value_on_non_control_raises() -> None:
    element = _control("<div></div>", "div")
    with pytest.raises(TypeError, match="can only be set on a form control"):
        element.field_value = "x"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<input type=che value=x>", "x", id="type-shorter-than-keyword"),
        pytest.param("<input type=checkboxx value=x>", "x", id="type-longer-than-keyword"),
        pytest.param("<input type value=x>", "x", id="valueless-type"),
    ],
)
def test_unusual_input_type_is_text_like(markup: str, expected: str) -> None:
    assert _control(markup, "input").field_value == expected


def test_option_value_strips_whitespace_only_text() -> None:
    select = _control("<select><option selected>   </option></select>", "select")
    assert select.field_value == ""  # ruff:ignore[compare-to-empty-string]


def test_select_skips_an_optgroup_wrapper() -> None:
    select = _control("<select><optgroup label=g><option value=a selected>A</optgroup></select>", "select")
    assert select.field_value == "a"


def test_delete_textarea_value_clears_it() -> None:
    field: Any = _control("<textarea>old</textarea>", "textarea")
    del field.field_value
    assert field.html == "<textarea></textarea>"


def test_delete_single_select_value_clears_all() -> None:
    select: Any = _control("<select><option value=r selected>Red<option value=g>Green</select>", "select")
    del select.field_value
    assert "selected" not in select.find_all("option")[0].attrs
