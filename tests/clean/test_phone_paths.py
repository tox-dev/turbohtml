from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector, PhoneNumber, PhoneNumbers, PhoneType


def _urls(text: str, regions: tuple[str, ...] = ("US",), *, valid: bool = True) -> list[str]:
    return [span.url for span in LinkDetector(phones=PhoneNumbers(regions=regions, require_valid=valid)).find(text)]


@pytest.mark.parametrize(
    ("text", "regions", "expected"),
    [
        pytest.param("460-1234", ("AG",), ["tel:+12684601234"], id="ag-local-number-rule-anchored-at-the-end"),
        pytest.param("91123456789", ("AR",), ["tel:+5491123456789"], id="ar-prefix-rule-matches-nothing"),
        pytest.param("800 123 4567", ("RU",), ["tel:+78001234567"], id="ru-prefix-guard-keeps-the-toll-free-8"),
        pytest.param("3788762", ("DE",), [], id="de-general-pattern-without-a-type"),
        pytest.param("(02) 3234 5678", ("PH",), ["tel:+63232345678"], id="ph-format-with-a-class-pattern"),
        pytest.param("20 7946 0958", ("GB",), [], id="gb-national-prefix-missing"),
        pytest.param("1 506 234 5678", ("CA",), ["tel:+15062345678"], id="own-code-from-a-non-main-region"),
        pytest.param("1-253-0000", ("US",), [], id="stripped-length-is-local-only"),
        pytest.param("1-2530-0000", ("US",), [], id="stripped-length-is-invalid"),
        pytest.param("268 555 1234", ("US",), [], id="routed-region-rejects"),
        pytest.param("+0 650 253 0000", ("US",), [], id="country-code-cannot-start-with-zero"),
        pytest.param("+999 123 4567", ("US",), [], id="unassigned-country-code"),
        pytest.param("+44 1", ("US",), [], id="too-short-after-the-country-code"),
        pytest.param("011 0 650 253 0000", ("US",), [], id="idd-followed-by-zero"),
        pytest.param("011 12", ("US",), [], id="idd-with-too-little-after-it"),
        pytest.param("+ 650 253 0000", ("US",), [], id="plus-then-space-reads-nothing"),
        pytest.param("(+1 650 253 0000)", ("US",), ["tel:+16502530000"], id="bracket-then-plus"),
        pytest.param("+(650) 253-0000", ("US",), ["tel:+16502530000"], id="plus-then-bracket"),
        pytest.param("(( 650 253 0000", ("US",), ["tel:+16502530000"], id="two-openers-with-space"),
        pytest.param("\uff08650\uff09 253-0000", ("US",), ["tel:+16502530000"], id="fullwidth-round-brackets"),
        pytest.param("\uff3b650\uff3d 253-0000", ("US",), ["tel:+16502530000"], id="fullwidth-square-brackets"),
        pytest.param("[650] 253-0000", ("US",), ["tel:+16502530000"], id="square-brackets"),
        pytest.param("650\uff0f253\uff0f0000", ("US",), ["tel:+16502530000"], id="fullwidth-slash"),
        pytest.param("650-253-0000 (1) (2) (3)", ("US",), ["tel:+16502530000"], id="three-bracket-pairs"),
        pytest.param("650-253-0000 (1) (2) (3) (4)", ("US",), ["tel:+16502530000"], id="fourth-pair-splits"),
        pytest.param("650-253-0000 )1( 2", ("US",), ["tel:+16502530000"], id="closer-before-opener-splits"),
        pytest.param("650-253-0000 () 1", ("US",), ["tel:+16502530000"], id="empty-pair-splits"),
        pytest.param("650-253-0000 (1 2", ("US",), ["tel:+16502530000"], id="unclosed-pair-splits"),
        pytest.param(
            "(530) 583-6985 x302/ x2303", ("US",), ["tel:+15305836985;ext=302"], id="second-number-after-space"
        ),
        pytest.param("(530) 583-6985 x302/", ("US",), ["tel:+15305836985;ext=302"], id="slash-at-the-end"),
        pytest.param(
            "(530) 583-6985 x302 - 5", ("US",), ["tel:+15305836985;ext=302"], id="marker-extension-inside-a-split"
        ),
        pytest.param(
            "650-253-0000 x - 5", ("US",), ["tel:+16502530000;ext=5"], id="marker-then-spaced-hyphen-extension"
        ),
        pytest.param("650-253-0000 \uff58123", ("US",), ["tel:+16502530000;ext=123"], id="fullwidth-x-marker"),
        pytest.param("650-253-0000 \uff03123", ("US",), ["tel:+16502530000;ext=123"], id="fullwidth-hash-marker"),
        pytest.param("650-253-0000 ~123", ("US",), ["tel:+16502530000;ext=123"], id="tilde-marker"),
        pytest.param("650-253-0000 \uff5e123", ("US",), ["tel:+16502530000;ext=123"], id="fullwidth-tilde-marker"),
        pytest.param("650-253-0000 ext 12 34", ("US",), ["tel:+16502530000;ext=12"], id="extension-digits-then-more"),
        pytest.param(
            "650-253-0000 ext " + "1" * 25,
            ("US",),
            ["tel:+16502530000;ext=" + "1" * 20],
            id="extension-cap-inside-a-run",
        ),
        pytest.param("650\u00ad253\u00ad0000", ("US",), ["tel:+16502530000"], id="soft-hyphen-separator"),
        pytest.param("650\u2060253\u20600000", ("US",), ["tel:+16502530000"], id="word-joiner-separator"),
        pytest.param("650\u2053253\u20530000", ("US",), ["tel:+16502530000"], id="swung-dash-separator"),
        pytest.param("650\u223c253\u223c0000", ("US",), ["tel:+16502530000"], id="tilde-operator-separator"),
        pytest.param("1\uff08650\uff09253-0000", ("US",), ["tel:+16502530000"], id="fullwidth-parens-inside"),
        pytest.param("1\uff3b650\uff3d253-0000", ("US",), ["tel:+16502530000"], id="fullwidth-brackets-inside"),
        pytest.param("1[650]253-0000", ("US",), ["tel:+16502530000"], id="square-brackets-inside"),
        pytest.param("650-253-0000 (1(2) 3", ("US",), ["tel:+16502530000"], id="opener-inside-a-pair-splits"),
        pytest.param(
            "650-253-0000 x1234567890 - 5", ("US",), ["tel:+16502530000"], id="marker-group-too-long-for-an-extension"
        ),
        pytest.param("0212345678", ("AU",), ["tel:+61212345678"], id="routerless-shared-code"),
        pytest.param("0549 886377", ("SM",), ["tel:+3780549886377"], id="sm-leading-zero-kept"),
        pytest.param(
            "references 650-253-0000", ("US",), ["tel:+16502530000"], id="word-longer-than-a-label-with-its-prefix"
        ),
        pytest.param("{650-253-0000", ("US",), ["tel:+16502530000"], id="brace-before"),
        pytest.param(
            "pages 1-5     (3 pages) 650-253-0000", ("US",), ["tel:+16502530000"], id="page-range-with-five-spaces"
        ),
        pytest.param("1-5 (\u0663 650-253-0000", ("US",), ["tel:+16502530000"], id="page-range-with-a-non-ascii-count"),
        pytest.param("1-\u0665 (3 650-253-0000", ("US",), ["tel:+16502530000"], id="page-range-with-a-non-ascii-page"),
        pytest.param("1-5 (x 650-253-0000", ("US",), ["tel:+16502530000"], id="page-range-without-a-count"),
        pytest.param(
            "2012-01-02 08:-1 650-253-0000",
            ("US",),
            ["tel:+12012010208", "tel:+16502530000"],
            id="minutes-tens-below-zero",
        ),
        pytest.param(
            "2012-01-02 08:0- 650-253-0000",
            ("US",),
            ["tel:+12012010208", "tel:+16502530000"],
            id="minutes-units-below-zero",
        ),
        pytest.param("01234567890", ("CH",), [], id="stripped-length-in-a-gap"),
        pytest.param("08 123 456", ("SE",), ["tel:+468123456"], id="format-longer-than-the-number"),
        pytest.param("1 650 253 0000 12345", ("US",), [], id="own-code-then-too-many-digits"),
        pytest.param(
            "(530) 583-6985 x302/x", ("US",), ["tel:+15305836985;ext=302"], id="second-number-marker-at-the-end"
        ),
        pytest.param(
            "650-253-0000 -x 123", ("US",), ["tel:+16502530000"], id="marker-after-a-hyphen-is-not-an-extension"
        ),
        pytest.param(
            "(530) 583-6985 -x302 - 5", ("US",), ["tel:+15305836985"], id="marker-after-a-hyphen-inside-a-split"
        ),
        pytest.param("101-02 08:00", ("US",), [], id="seven-digits-before-the-hour"),
        pytest.param(
            "1999-01-02 08:00 650-253-0000", ("US",), ["tel:+16502530000"], id="timestamp-in-the-previous-century"
        ),
        pytest.param("{ref 650-253-0000", ("US",), [], id="label-after-a-brace"),
        pytest.param("1-5 (\u0663) 650-253-0000", ("US",), ["tel:+16502530000"], id="page-count-not-ascii"),
        pytest.param("1-5 (x)650-253-0000", ("US",), [], id="page-count-not-a-digit"),
        pytest.param("594 10 12 34", ("GF",), [], id="own-code-number-valid-as-a-whole"),
        pytest.param(
            "(530) 583-6985 x302/x2303 and more", ("US",), ["tel:+15305836985;ext=302"], id="second-number-then-text"
        ),
    ],
)
def test_readings_reach_each_rule(text: str, regions: tuple[str, ...], expected: list[str]) -> None:
    assert _urls(text, regions) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("3/10/11 650-253-0000", ["tel:+16502530000"], id="two-digit-year"),
        pytest.param("31/8/2011 650-253-0000", ["tel:+16502530000"], id="day-first"),
        pytest.param("10/12/82 650-253-0000", ["tel:+16502530000"], id="short-year"),
        pytest.param("40/40/2011 650-253-0000", ["tel:+16502530000"], id="middle-part-over-39-is-not-a-date"),
        pytest.param("3/10/3011 650-253-0000", ["tel:+16502530000"], id="any-two-digit-year-part"),
        pytest.param("45/10/2011 650-253-0000", ["tel:+16502530000"], id="first-part-is-the-last-digit"),
        pytest.param("3/10/2 650-253-0000", ["tel:+16502530000"], id="one-digit-year-is-not-a-date"),
        pytest.param("3//10/2011 650-253-0000", ["tel:+13102011", "tel:+16502530000"], id="double-slash-is-not-a-date"),
        pytest.param("123/10/2011 650-253-0000", ["tel:+16502530000"], id="three-digit-first-part"),
        pytest.param("2012/01/02 08:00 650-253-0000", ["tel:+16502530000"], id="slash-timestamp"),
        pytest.param("20120102 08:00 650-253-0000", ["tel:+16502530000"], id="compact-timestamp"),
        pytest.param("2012-01/02 08:00 650-253-0000", ["tel:+16502530000"], id="mixed-date-separators"),
        pytest.param("2012-0102 08:00 650-253-0000", ["tel:+16502530000"], id="one-separator"),
        pytest.param("12012-01-02 08:00 650-253-0000", ["tel:+16502530000"], id="year-is-the-tail-of-a-longer-group"),
        pytest.param("2012-01-02  08:00 650-253-0000", ["tel:+16502530000"], id="hour-after-two-spaces"),
        pytest.param(
            "2012-01-02 08:60 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="minutes-out-of-range"
        ),
        pytest.param(
            "2012-01-02 08:0x 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="minutes-not-digits"
        ),
        pytest.param("2012-01-02 08:", ["tel:+12012010208"], id="colon-without-minutes"),
        pytest.param("2012-01-02 08:0", ["tel:+12012010208"], id="one-digit-minutes"),
        pytest.param("2012-01-02 08:\u0660\u0660", ["tel:+12012010208"], id="minutes-not-ascii"),
        pytest.param(
            "3012-01-02 08:00 650-253-0000", ["tel:+13012010208", "tel:+16502530000"], id="year-outside-the-century"
        ),
        pytest.param(
            "2012-21-02 08:00 650-253-0000", ["tel:+12012210208", "tel:+16502530000"], id="month-out-of-range"
        ),
        pytest.param("2012-01-42 08:00 650-253-0000", ["tel:+12012014208", "tel:+16502530000"], id="day-out-of-range"),
        pytest.param("2012-01-02 38:00 650-253-0000", ["tel:+12012010238", "tel:+16502530000"], id="hour-out-of-range"),
        pytest.param("2012-01-02 8:00 650-253-0000", ["tel:+16502530000"], id="one-digit-hour"),
        pytest.param("2012.01.02 08:00 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="dotted-date"),
        pytest.param("2012--01-02 08:00 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="double-hyphen"),
        pytest.param(
            "201-201-02 08:00 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="separator-off-the-boundary"
        ),
        pytest.param(
            "2012-01-02-08:00 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="hyphen-before-the-hour"
        ),
        pytest.param(
            "2012-01-02 08\u00a0\u00a0:00 650-253-0000",
            ["tel:+12012010208", "tel:+16502530000"],
            id="hour-after-non-ascii-space",
        ),
        pytest.param("01-02 08:00", [], id="too-few-digits-for-a-stamp"),
        pytest.param("12.34.56.7 650-253-0000", ["tel:+16502530000"], id="ipv4-short-last-octet"),
        pytest.param("1.2.3 650-253-0000", ["tel:+16502530000"], id="three-dotted-groups-are-not-ipv4"),
        pytest.param("+1.2.3.4", [], id="plus-before-dotted-groups"),
        pytest.param("Order#12345 650-253-0000", ["tel:+16502530000"], id="label-through-hash"),
        pytest.param("Ref\t650-253-0000", [], id="label-through-tab"),
        pytest.param("ref-650-253-0000", [], id="label-through-hyphen"),
        pytest.param("abcdefghijklmnop 650-253-0000", ["tel:+16502530000"], id="word-longer-than-a-label"),
        pytest.param("ref2 650-253-0000", ["tel:+16502530000"], id="label-followed-by-a-digit"),
    ],
)
def test_poison_shapes(text: str, expected: list[str]) -> None:
    assert _urls(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("4111 1111 1111 1111 110", [], id="four-by-four-plus-three"),
        pytest.param("3782 822463 10005", [], id="amex-shape"),
        pytest.param("3056 930902 5904", [], id="four-six-four"),
        pytest.param("4111111111111111", [], id="single-run"),
        pytest.param("4111 1111 1111 1112", ["tel:+494111", "tel:+49111111111112"], id="luhn-fails"),
        pytest.param("4111 1111 1111 11111", ["tel:+494111", "tel:+491111111111111"], id="not-a-card-shape"),
    ],
)
def test_card_shapes(text: str, expected: list[str]) -> None:
    assert _urls(text, ("DE",), valid=False) == expected


def test_a_single_group_past_the_card_length() -> None:
    assert _urls("12345678901234567890", ("DE",), valid=False) == []


def test_a_run_of_full_groups_stops_at_the_length_cap() -> None:
    assert _urls(" ".join(["12345678901234567890"] * 14) + " 650-253-0000") == ["tel:+16502530000"]


def test_a_group_over_twenty_digits_at_the_end() -> None:
    assert _urls("650-253-0000 " + "1" * 21) == ["tel:+16502530000"]


@pytest.mark.parametrize(
    ("text", "starts", "valid"),
    [
        pytest.param("(+1 650 253 0000)", [0], True, id="two-lead-groups"),
        pytest.param("(+(650) 253-0000", [2], True, id="three-lead-characters-keep-the-last-two"),
        pytest.param("(++650 253 0000", [], True, id="three-lead-characters-without-punctuation"),
        pytest.param("(--- 650-253-0000", [0], True, id="four-punctuation-after-the-lead"),
        pytest.param("(---- 650-253-0000", [6], True, id="five-punctuation-drop-the-lead"),
        pytest.param("+a650-253-0000", [2], False, id="letter-after-the-lead"),
    ],
)
def test_lead_groups(text: str, starts: list[int], valid: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    assert [
        span.start for span in LinkDetector(phones=PhoneNumbers(regions=("US",), require_valid=valid)).find(text)
    ] == starts


def test_closer_after_the_leading_part_splits_the_run() -> None:
    assert _urls("(650) 253)-0000", valid=False) == ["tel:+12530000"]


@pytest.mark.parametrize(
    ("country_code", "national_number", "region"),
    [
        pytest.param(1, "2685551234", "AG", id="routed-region-without-a-type"),
        pytest.param(1, "7218478877", "SX", id="routed-region-outside-its-general-pattern"),
    ],
)
def test_value_check_rejects_a_routed_region_whose_plan_rejects(
    country_code: int, national_number: str, region: str
) -> None:
    with pytest.raises(ValueError, match="no number"):
        PhoneNumber(country_code, national_number, None, region, PhoneType.FIXED_LINE)


def test_leading_zeros_are_capped_at_ten() -> None:
    assert [
        span.phone.national_number
        for span in LinkDetector(phones=PhoneNumbers(require_valid=False)).find("+62 " + "0" * 12 + "12345")
        if span.phone
    ] == ["0" * 10 + "12345"]


def test_all_zero_number_in_possible_mode() -> None:
    assert [
        span.phone.national_number
        for span in LinkDetector(phones=PhoneNumbers(regions=("US",), require_valid=False)).find("000-000-0000")
        if span.phone
    ] == ["0000000000"]


def test_general_only_number_in_possible_mode_reports_its_region() -> None:
    assert [
        (span.phone.region, span.phone.type)
        for span in LinkDetector(phones=PhoneNumbers(regions=("DE",), require_valid=False)).find("3788762")
        if span.phone
    ] == [("DE", PhoneType.UNKNOWN)]


def test_value_check_rejects_leading_zeros_past_the_cap() -> None:
    with pytest.raises(ValueError, match="no number"):
        PhoneNumber(62, "0" * 12 + "12345", None, "ID", PhoneType.UNKNOWN)


def test_value_check_rejects_a_char_below_zero() -> None:
    with pytest.raises(ValueError, match="ASCII digits"):
        PhoneNumber(1, "650-530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE)
