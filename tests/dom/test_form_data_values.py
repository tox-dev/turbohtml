"""form_data: the submitted value of each control, per the WHATWG entry-list and value sanitization rules."""

from __future__ import annotations

import pytest

from turbohtml import Element, parse
from turbohtml.build import E


def _submitted(markup: str) -> list[tuple[str, str]]:
    form = parse(f"<form>{markup}</form>").find("form")
    assert form is not None
    return form.form_data()


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        pytest.param('value="a&#10;b&#13;c"', "abc", id="text-strips-newlines"),
        pytest.param('value="a&#13;b&#10;c&#10;&#13;d"', "abcd", id="text-strips-every-newline"),
        pytest.param('value="a&#9;b&#11;c&#12;d"', "a\tb\x0bc\x0cd", id="text-keeps-tab-and-form-feed"),
        pytest.param("", "", id="typeless-absent-value-is-empty"),
        pytest.param('type value="a&#10;b"', "ab", id="valueless-type-is-text"),
        pytest.param('type=bogus value="a&#10;b"', "ab", id="unknown-type-is-text"),
        pytest.param('type=SEARCH value=" a&#10;b "', " ab ", id="search-keeps-spaces"),
        pytest.param('type=password value="p&#13;q"', "pq", id="password-strips-newlines"),
        pytest.param('type=url value=" http://x/&#10; "', "http://x/", id="url-trims"),
        pytest.param('type=email value=" a@b.c "', "a@b.c", id="email-trims"),
        pytest.param('type=email multiple value=" a@b.c , d@e.f,"', "a@b.c,d@e.f", id="email-list-trims-each"),
        pytest.param('type=email multiple value=",x, "', ",x,", id="email-list-keeps-empty-tokens"),
        pytest.param("type=email multiple", "", id="email-list-absent"),
        pytest.param('type=hidden value="a&#10;b"', "a\nb", id="hidden-verbatim"),
        pytest.param("type=color value=RED", "RED", id="color-verbatim"),
        pytest.param("type=text", "", id="absent-value-is-empty"),
        pytest.param("type=url", "", id="url-absent"),
        pytest.param('type=url value=" &#10; "', "", id="url-all-whitespace"),
        pytest.param('type=url value="   "', "", id="url-spaces"),
        pytest.param('type=url value=" a "', "a", id="url-trimmed-single"),
    ],
)
def test_form_data_text_states(attrs: str, expected: str) -> None:
    assert _submitted(f"<input name=x {attrs}>") == [("x", expected)]


@pytest.mark.parametrize("kind", ["text", "url", "email"])
@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("a", id="single"),
        pytest.param("hello", id="ascii"),
        pytest.param("abcXYZ" * 512, id="long-ascii"),
        pytest.param("h\x7fllo", id="ascii-boundary"),
        pytest.param("h\x80llo", id="latin-boundary"),
        pytest.param("hÿllo", id="latin"),
        pytest.param("hĀllo", id="bmp-boundary"),
        pytest.param("h漢llo", id="bmp"),
        pytest.param("h😀llo", id="astral"),
        pytest.param("a\tb漢c", id="control-before-unicode"),
    ],
)
def test_form_data_text_value_width(kind: str, value: str) -> None:
    assert _submitted(f'<input name=x type="{kind}" value="{value}">') == [("x", value)]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("1.5e3", "1.5e3", id="exponent"),
        pytest.param("-1E+2", "-1E+2", id="signed-exponent"),
        pytest.param("1e-2", "1e-2", id="negative-exponent"),
        pytest.param(".5", ".5", id="leading-point"),
        pytest.param("abc", "", id="not-a-number"),
        pytest.param(" 1", "", id="whitespace"),
        pytest.param("+1", "", id="plus-sign"),
        pytest.param("1.", "", id="point-without-fraction"),
        pytest.param("-", "", id="sign-only"),
        pytest.param("1e", "", id="exponent-without-digits"),
        pytest.param("1x", "", id="trailing-garbage"),
    ],
)
def test_form_data_number_value(value: str, expected: str) -> None:
    assert _submitted(f'<input type=number name=x value="{value}">') == [("x", expected)]


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        pytest.param("", "50", id="absent-is-midpoint"),
        pytest.param("value=abc", "50", id="invalid-is-midpoint"),
        pytest.param("value=50.0", "50.0", id="valid-kept-as-written"),
        pytest.param("value=1e999", "1e999", id="valid-past-double-range-kept"),
        pytest.param("min=0 max=100 step=20 value=50", "60", id="step-tie-rounds-up"),
        pytest.param("value=150", "100", id="clamped-to-max"),
        pytest.param("value=-5", "0", id="clamped-to-min"),
        pytest.param("min=10 max=5", "10", id="max-below-min-defaults-to-min"),
        pytest.param("min=10 max=5 value=20", "20", id="max-below-min-no-overflow"),
        pytest.param("min=0 max=1 step=0.1 value=0.35", "0.4", id="decimal-step-tie-rounds-up"),
        pytest.param("min=0 max=1 step=0.1 value=0.33", "0.3", id="decimal-step-rounds-down"),
        pytest.param("min=0 max=1 step=0.1 value=0.3", "0.3", id="aligned-decimal-step"),
        pytest.param("step=any value=3.3", "3.3", id="step-any"),
        pytest.param("step=ANY value=3.3", "3.3", id="step-any-case-insensitive"),
        pytest.param("min=0 step value=3.5", "4", id="valueless-step-is-one"),
        pytest.param("min=0 step=x value=3.5", "4", id="invalid-step-is-one"),
        pytest.param("min=0 step=-2 value=3.5", "4", id="negative-step-is-one"),
        pytest.param("step=2 value=3", "3", id="value-attribute-is-step-base"),
        pytest.param("min=1 step=2 value=4", "5", id="min-is-step-base"),
        pytest.param('min=" +1.5e0x" max=2.5 step=any', "2", id="lenient-min-parse"),
        pytest.param("min=.5 max=1.5 step=any", "1", id="lenient-leading-point"),
        pytest.param("min=5. max=7 step=any", "6", id="lenient-point-without-fraction"),
        pytest.param("min=5.x max=7 step=any", "6", id="lenient-point-before-garbage"),
        pytest.param("min=. max=4", "2", id="lenient-lone-point"),
        pytest.param("min=.x max=4", "2", id="lenient-point-then-garbage"),
        pytest.param("min=-1e400 max=4", "2", id="negative-overflowing-min-is-default"),
        pytest.param("min=10 max=5 step=3 value=20", "19", id="max-below-min-steps-without-upper-bound"),
        pytest.param("min=1e1x max=12 step=any", "11", id="lenient-exponent"),
        pytest.param("min=2e-1 max=4E+1 step=any", "20.1", id="lenient-signed-exponent"),
        pytest.param("min=1ex max=3 step=any", "2", id="lenient-exponent-without-digits"),
        pytest.param("min=1e max=3 step=any", "2", id="lenient-trailing-exponent-marker"),
        pytest.param("min=1e- max=3 step=any", "2", id="lenient-trailing-exponent-sign"),
        pytest.param("min=-0 max=0", "0", id="negative-zero-is-zero"),
        pytest.param("min=1e400 max=4", "2", id="overflowing-min-is-default"),
        pytest.param("min=x max=-", "50", id="unparsable-bounds-are-default"),
        pytest.param("min=- max=+", "50", id="sign-only-bounds-are-default"),
        pytest.param("min=-10 max=-5", "-7", id="negative"),
        pytest.param("min=0 max=1e30", "5e+29", id="exponent-form"),
        pytest.param("min=0 max=3e30", "1.5e+30", id="exponent-form-with-fraction"),
        pytest.param("min=0 max=2e-7 step=any", "1e-7", id="negative-exponent-form"),
        pytest.param("min=0.000001 max=0.000002", "0.000001", id="small-fraction"),
        pytest.param("min=0 max=1e-15 step=1e-16", "5e-16", id="past-fifteen-decimals"),
        pytest.param("max=0.4 value=-0.5", "0", id="no-step-fits-keeps-clamp"),
    ],
)
def test_form_data_range_value(attrs: str, expected: str) -> None:
    assert _submitted(f"<input type=range name=x {attrs}>") == [("x", expected)]


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        pytest.param("date", "2024-02-29", "2024-02-29", id="date-leap-day"),
        pytest.param("date", "2000-02-29", "2000-02-29", id="date-leap-century"),
        pytest.param("date", "1900-02-29", "", id="date-non-leap-century"),
        pytest.param("date", "2023-02-29", "", id="date-non-leap-year"),
        pytest.param("date", "12024-01-31", "12024-01-31", id="date-five-digit-year"),
        pytest.param("date", "0000-01-01", "", id="date-year-zero"),
        pytest.param("date", "224-01-01", "", id="date-short-year"),
        pytest.param("date", "2024-13-01", "", id="date-month-13"),
        pytest.param("date", "2024-04-31", "", id="date-day-past-month"),
        pytest.param("date", "2024-04-00", "", id="date-day-zero"),
        pytest.param("date", "2024-1-01", "", id="date-one-digit-month"),
        pytest.param("date", "2024-1", "", id="date-truncated-month"),
        pytest.param("date", "2024-x1-01", "", id="date-non-digit-month"),
        pytest.param("date", "2024/01/01", "", id="date-wrong-separator"),
        pytest.param("date", "2024-01/01", "", id="date-wrong-day-separator"),
        pytest.param("date", "2024-01-01x", "", id="date-trailing-text"),
        pytest.param("month", "2024-12", "2024-12", id="month"),
        pytest.param("month", "2024-00", "", id="month-zero"),
        pytest.param("week", "2026-W53", "2026-W53", id="week-53-thursday-start"),
        pytest.param("week", "2020-W53", "2020-W53", id="week-53-leap-wednesday-start"),
        pytest.param("week", "2000-W52", "2000-W52", id="week-cycle-year"),
        pytest.param("week", "2025-W53", "", id="week-53-in-52-week-year"),
        pytest.param("week", "2024-W00", "", id="week-zero"),
        pytest.param("week", "2024-w01", "", id="week-lowercase-w"),
        pytest.param("week", "2024W01", "", id="week-no-dash"),
        pytest.param("week", "20-W01", "", id="week-short-year"),
        pytest.param("week", "2024-W01x", "", id="week-trailing-text"),
        pytest.param("time", "23:59", "23:59", id="time"),
        pytest.param("time", "12:00:05.5", "12:00:05.5", id="time-fraction"),
        pytest.param("time", "24:00", "", id="time-hour-24"),
        pytest.param("time", "12-00", "", id="time-wrong-separator"),
        pytest.param("time", "12:60", "", id="time-minute-60"),
        pytest.param("time", "12:00:60", "", id="time-second-60"),
        pytest.param("time", "12:00:00.", "", id="time-empty-fraction"),
        pytest.param("time", "12:00:00.1234", "", id="time-four-digit-fraction"),
        pytest.param("datetime-local", "2024-01-01 10:00:00.000", "2024-01-01T10:00", id="datetime-zero-seconds"),
        pytest.param("datetime-local", "2024-01-01T10:00:05", "2024-01-01T10:00:05", id="datetime-seconds"),
        pytest.param("datetime-local", "2024-01-01T10:00:05.500", "2024-01-01T10:00:05.5", id="datetime-fraction"),
        pytest.param("datetime-local", "2024-01-01T10:00:00.050", "2024-01-01T10:00:00.05", id="datetime-subsecond"),
        pytest.param("datetime-local", "2024-01-01t10:00", "", id="datetime-lowercase-t"),
        pytest.param("datetime-local", "2024-01-01T10:00x", "", id="datetime-trailing-text"),
        pytest.param("datetime-local", "2024-02-30T10:00", "", id="datetime-invalid-date"),
    ],
)
def test_form_data_date_and_time_value(kind: str, value: str, expected: str) -> None:
    assert _submitted(f'<input type={kind} name=x value="{value}">') == [("x", expected)]


def test_form_data_skips_controls_inside_a_datalist() -> None:
    assert _submitted("<datalist><input name=a value=1></datalist><input name=b value=2>") == [("b", "2")]


def test_form_data_normalizes_textarea_newlines() -> None:
    form: Element = E.form(E.textarea({"name": "t"}, "a\r\nb\rc\nd\r"))
    assert form.form_data() == [("t", "a\nb\nc\nd\n")]
