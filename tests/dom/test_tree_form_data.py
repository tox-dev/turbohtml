"""Serializing a form's successful controls with Element.form_data."""

from __future__ import annotations

import pytest

from turbohtml import Element, parse


def _form(markup: str) -> Element:
    form = parse(markup).find("form")
    assert form is not None
    return form


def test_a_blank_string_named_control_is_skipped() -> None:
    form = _form("<form></form>")
    form.append(Element("input", {"name": "", "value": "y"}))  # an empty-string name is still no name
    assert form.form_data() == []


def test_text_and_hidden_inputs_are_submitted() -> None:
    form = _form("<form><input name=q value=hello><input name=tok type=hidden value=abc></form>")
    assert form.form_data() == [("q", "hello"), ("tok", "abc")]


def test_a_missing_value_submits_empty() -> None:
    assert _form("<form><input name=q></form>").form_data() == [("q", "")]


def test_unnamed_controls_are_skipped() -> None:
    assert _form("<form><input value=x><input name='' value=y><input name value=z></form>").form_data() == []


def test_controls_nested_in_plain_wrappers_are_found() -> None:
    form = _form("<form><div><p><input name=q value=ok></p></div></form>")
    assert form.form_data() == [("q", "ok")]


def test_checkbox_only_submits_when_checked() -> None:
    form = _form("<form><input name=a type=checkbox value=1 checked><input name=b type=checkbox value=2></form>")
    assert form.form_data() == [("a", "1")]


def test_checkbox_without_value_submits_on() -> None:
    assert _form("<form><input name=a type=checkbox checked></form>").form_data() == [("a", "on")]


def test_radio_only_submits_the_checked_one() -> None:
    form = _form(
        "<form><input name=s type=radio value=s><input name=s type=radio value=m checked>"
        "<input name=s type=radio value=l></form>"
    )
    assert form.form_data() == [("s", "m")]


@pytest.mark.parametrize("button_type", ["submit", "reset", "button", "image", "file"])
def test_buttons_and_files_are_skipped(button_type: str) -> None:
    form = _form(f"<form><input name=b type={button_type} value=x><input name=q value=ok></form>")
    assert form.form_data() == [("q", "ok")]


def test_button_element_is_skipped() -> None:
    assert _form("<form><button name=b value=x>Go</button></form>").form_data() == []


def test_textarea_submits_its_text() -> None:
    assert _form("<form><textarea name=bio>hi there</textarea></form>").form_data() == [("bio", "hi there")]


def test_disabled_controls_are_skipped() -> None:
    assert _form("<form><input name=a value=1 disabled><input name=b value=2></form>").form_data() == [("b", "2")]


def test_a_disabled_fieldset_skips_its_controls() -> None:
    form = _form("<form><fieldset disabled><input name=a value=1></fieldset><input name=b value=2></form>")
    assert form.form_data() == [("b", "2")]


def test_a_disabled_fieldset_keeps_controls_inside_its_first_legend() -> None:
    form = _form(
        "<form><fieldset disabled><legend><input name=a value=1></legend><input name=b value=2></fieldset></form>"
    )
    assert form.form_data() == [("a", "1")]


def test_a_disabled_fieldset_skips_controls_inside_later_legends() -> None:
    form = _form(
        "<form><fieldset disabled><legend>x</legend><legend><input name=a value=1></legend>"
        "<input name=b value=2></fieldset></form>"
    )
    assert form.form_data() == []


def test_an_enabled_fieldset_keeps_its_controls() -> None:
    form = _form("<form><fieldset><input name=a value=1></fieldset></form>")
    assert form.form_data() == [("a", "1")]


def test_single_select_submits_the_selected_option() -> None:
    form = _form("<form><select name=c><option value=r>Red<option value=g selected>Green</select></form>")
    assert form.form_data() == [("c", "g")]


def test_single_select_defaults_to_the_first_option() -> None:
    form = _form("<form><select name=c><option value=r>Red<option value=g>Green</select></form>")
    assert form.form_data() == [("c", "r")]


def test_single_select_skips_a_disabled_default() -> None:
    form = _form("<form><select name=c><option value=r disabled>Red<option value=g>Green</select></form>")
    assert form.form_data() == [("c", "g")]


def test_an_empty_select_submits_nothing() -> None:
    assert _form("<form><select name=c></select></form>").form_data() == []


def test_multiple_select_submits_each_selected_option() -> None:
    form = _form(
        "<form><select name=t multiple><option value=a selected>A<option value=b>"
        "<option value=c selected>C</select></form>"
    )
    assert form.form_data() == [("t", "a"), ("t", "c")]


def test_multiple_select_skips_disabled_options() -> None:
    form = _form(
        "<form><select name=t multiple><option value=a selected disabled>A<option value=b selected>B</select></form>"
    )
    assert form.form_data() == [("t", "b")]


def test_single_select_default_skips_an_optgroup_disabled_option() -> None:
    form = _form(
        "<form><select name=s><optgroup disabled><option value=a>A</optgroup><option value=b>B</select></form>"
    )
    assert form.form_data() == [("s", "b")]


def test_multiple_select_skips_optgroup_disabled_options() -> None:
    form = _form(
        "<form><select name=t multiple><optgroup disabled><option value=a selected>A</optgroup>"
        "<option value=b selected>B</select></form>"
    )
    assert form.form_data() == [("t", "b")]


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        pytest.param("size=1", [("s", "a")], id="display-size-1"),
        pytest.param("size=4", [], id="display-size-4"),
        pytest.param('size=" 4"', [], id="leading-whitespace-parsed"),
        pytest.param('size="  "', [("s", "a")], id="all-whitespace-falls-to-default"),
        pytest.param('size="x"', [("s", "a")], id="non-numeric-falls-to-default"),
        pytest.param('size="-1"', [("s", "a")], id="leading-non-digit-falls-to-default"),
        pytest.param('size="99999"', [], id="huge-size-clamped-not-one"),
        pytest.param("size", [("s", "a")], id="valueless-falls-to-default"),
    ],
)
def test_select_default_first_only_when_display_size_is_one(attr: str, expected: list[tuple[str, str]]) -> None:
    form = _form(f"<form><select name=s {attr}><option value=a><option value=b></select></form>")
    assert form.form_data() == expected


def test_select_with_only_a_disabled_selected_option_submits_nothing() -> None:
    form = _form("<form><select name=s><option value=a disabled selected><option value=b></select></form>")
    assert form.form_data() == []


def test_form_data_ignores_controls_inside_a_template() -> None:
    form = _form("<form><template><input name=t value=1></template><input name=a value=2></form>")
    assert form.form_data() == [("a", "2")]


def test_form_data_ignores_controls_nested_deep_inside_a_template() -> None:
    form = _form("<form><template><div><input name=t value=1></div></template><input name=a value=2></form>")
    assert form.form_data() == [("a", "2")]


def test_form_data_skips_a_trailing_template() -> None:
    form = _form("<form><input name=a value=2><template><input name=t value=1></template></form>")
    assert form.form_data() == [("a", "2")]


def test_option_value_falls_back_to_text() -> None:
    form = _form("<form><select name=c><option selected> Green </select></form>")
    assert form.form_data() == [("c", "Green")]


def test_pairs_keep_document_order() -> None:
    form = _form("<form><input name=z value=1><input name=a value=2></form>")
    assert form.form_data() == [("z", "1"), ("a", "2")]


def test_form_data_on_a_non_form_raises() -> None:
    element = parse("<div></div>").find("div")
    assert element is not None
    with pytest.raises(TypeError, match="can only be called on a form element"):
        element.form_data()
