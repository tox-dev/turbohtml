from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import LinkDetector, PhoneGrouping, PhoneNumbers

_ALL: Final = (PhoneGrouping.ANY, PhoneGrouping.STRICT, PhoneGrouping.EXACT)
_LOOSE: Final = (PhoneGrouping.ANY, PhoneGrouping.STRICT)
_NONE: Final = (PhoneGrouping.ANY,)
_UNBROKEN: Final = (PhoneGrouping.ANY, PhoneGrouping.EXACT)


@pytest.mark.parametrize(
    ("region", "text", "accepting"),
    [
        pytest.param("US", "(415) 666-7777", _ALL, id="us-national-format"),
        pytest.param("US", "415-666-7777", _ALL, id="us-hyphens"),
        pytest.param("US", "415.666.7777", _ALL, id="us-dots"),
        pytest.param("US", "4156667777", _ALL, id="us-unbroken"),
        pytest.param("US", "1 415 666 7777", _ALL, id="us-own-code"),
        pytest.param("US", "1415 666 7777", _ALL, id="us-code-glued-to-the-area-code"),
        pytest.param("US", "+1 (415) 666-7777", _ALL, id="us-plus"),
        pytest.param("US", "+14156667777", _ALL, id="us-e164"),
        pytest.param("US", "011 1 415 666 7777", _ALL, id="us-idd"),
        pytest.param("US", "(415) 666-7777 x 12", _ALL, id="us-extension"),
        pytest.param("US", "+1/415/666-7777", _ALL, id="us-slash-after-the-code-is-not-a-date"),
        pytest.param("US", "415 6667777", _LOOSE, id="us-last-groups-merged"),
        pytest.param("US", "415-6667777", _LOOSE, id="us-last-groups-merged-with-hyphen"),
        pytest.param("US", "415666 7777", _NONE, id="us-first-groups-merged"),
        pytest.param("US", "41 566 67777", _NONE, id="us-shifted-groups"),
        pytest.param("US", "4 15 666 7777", _NONE, id="us-split-area-code"),
        pytest.param("US", "415/666/7777", _NONE, id="us-two-slashes"),
        pytest.param("GB", "020 7946 0958", _ALL, id="gb-national-format"),
        pytest.param("GB", "0207 946 0958", _ALL, id="gb-alternate-format"),
        pytest.param("GB", "+44 (0)20 7946 0958", _ALL, id="gb-optional-prefix-in-brackets"),
        pytest.param("GB", "0 20 7946 0958", _ALL, id="gb-prefix-apart"),
        pytest.param("GB", "020 79460958", _LOOSE, id="gb-last-groups-merged"),
        pytest.param("DE", "030 12345678", _ALL, id="de-national-format"),
        pytest.param("DE", "030 1234 5678", _ALL, id="de-alternate-format"),
        pytest.param("DE", "030 123 456 78", _ALL, id="de-alternate-format-with-more-groups"),
        pytest.param("DE", "030/12345678", _ALL, id="de-one-slash"),
        pytest.param("DE", "030/1234/5678", _NONE, id="de-two-slashes"),
        pytest.param("DE", "0151 2345 6789", _NONE, id="de-mobile-grouped-wrongly"),
        pytest.param("DE", "01512 3456789", _ALL, id="de-mobile-grouped-right"),
        pytest.param("FR", "01 23 45 67 89", _ALL, id="fr-pairs"),
        pytest.param("FR", "01 2345 6789", _LOOSE, id="fr-pairs-merged"),
        pytest.param("FR", "+33 123 456 789", _NONE, id="fr-triples"),
        pytest.param("AR", "011 15-2345-6789", _NONE, id="ar-mobile-token-only-any"),
        pytest.param("AR", "+54 9 11 2345-6789", _ALL, id="ar-mobile-international"),
        pytest.param("US", "1/415/666-7777", _ALL, id="us-own-code-then-one-slash"),
        pytest.param("US", "1/415/666/7777", _NONE, id="us-own-code-then-two-slashes"),
        pytest.param("US", "+1 415/666/7777", _NONE, id="us-plus-and-area-code-before-two-slashes"),
        pytest.param("AG", "268 460-1234", _ALL, id="ag-area-code-written"),
        pytest.param("AG", "460 1234", _NONE, id="ag-transformed-prefix-groups-absent"),
        pytest.param("AG", "4601234", _UNBROKEN, id="ag-transformed-unbroken-run"),
        pytest.param("ES", "612345678", _ALL, id="es-no-national-prefix-unbroken"),
        pytest.param("ES", "61234 5678", _LOOSE, id="es-no-national-prefix-merged"),
        pytest.param("AC", "62889", _ALL, id="ac-region-without-formats"),
        pytest.param("SM", "0549 912345", _ALL, id="sm-area-code-written"),
        pytest.param("SM", "91 23 45", _NONE, id="sm-transformed-prefix-groups-regrouped"),
        pytest.param("SM", "912345", _UNBROKEN, id="sm-transformed-unbroken-run"),
        pytest.param("NF", "2 2123", _NONE, id="nf-one-digit-transform"),
        pytest.param("DE", "(02) 3234 5678", _NONE, id="de-bracketed-prefix-shorter-than-the-number"),
    ],
)
def test_grouping_leniencies_follow_the_matcher(region: str, text: str, accepting: tuple[PhoneGrouping, ...]) -> None:
    assert {
        grouping: [
            span.text for span in LinkDetector(phones=PhoneNumbers(regions=(region,), grouping=grouping)).find(text)
        ]
        for grouping in PhoneGrouping
    } == {grouping: [text] if grouping in accepting else [] for grouping in PhoneGrouping}


def test_grouping_applies_inside_prose() -> None:
    assert [
        span.text
        for span in LinkDetector(phones=PhoneNumbers(regions=("US",), grouping=PhoneGrouping.EXACT)).find(
            "call (415) 666-7777 or 415666 7777 now"
        )
    ] == ["(415) 666-7777"]
