from __future__ import annotations

import copy
import pickle  # ruff:ignore[suspicious-pickle-import]  # this test pickles its own values
import random
import re
from typing import Final

import pytest

from turbohtml.clean import LinkDetector, Linkify, PhoneGrouping, PhoneNumbers, linkify

_CH: Final = PhoneNumbers(regions=("CH",))
_CH_COLLAPSED: Final = PhoneNumbers(regions=("CH",), collapse_whitespace=True)
_US: Final = PhoneNumbers(regions=("US",))
_US_COLLAPSED: Final = PhoneNumbers(regions=("US",), collapse_whitespace=True)


def _found(phones: PhoneNumbers, text: str) -> list[str]:
    return [span.url for span in LinkDetector(phones=phones).find(text)]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("+41     79434     3254", id="five-spaces"),
        pytest.param("+41\n79434\n3254", id="newline"),
        pytest.param("+41\t79434\t3254", id="tab"),
        pytest.param("+41\r\n79434\r\n3254", id="carriage-return"),
        pytest.param("+41\f79434\f3254", id="form-feed"),
        pytest.param("+41 \n 79434 \n 3254", id="mixed-run"),
        pytest.param("079\n434\n32\n54", id="national"),
    ],
)
def test_collapse_whitespace_reads_a_run_as_one_space(text: str) -> None:
    assert _found(_CH_COLLAPSED, text) == ["tel:+41794343254"]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("+41     79434     3254", id="five-spaces"),
        pytest.param("+41\n79434\n3254", id="newline"),
        pytest.param("+41\t79434\t3254", id="tab"),
        pytest.param("079\n434\n32\n54", id="national"),
    ],
)
def test_default_stops_at_the_separator_limit(text: str) -> None:
    assert _found(_CH, text) == []


@pytest.mark.parametrize("separator", [pytest.param("\u00a0", id="nbsp"), pytest.param("\u3000", id="ideographic")])
def test_collapse_whitespace_leaves_the_spaces_html_keeps(separator: str) -> None:
    assert _found(_CH_COLLAPSED, f"+41{separator * 5}79434{separator * 5}3254") == []
    assert _found(_CH_COLLAPSED, f"+41{separator * 4}79434{separator * 4}3254") == ["tel:+41794343254"]


def test_collapse_whitespace_leaves_a_vertical_tab() -> None:
    assert _found(_CH_COLLAPSED, "+41\v79434 3254") == []


def test_collapse_whitespace_reaches_a_lead_over_a_run() -> None:
    text = f"+{' ' * 300}41 79434 3254"
    assert [(span.text, span.url) for span in LinkDetector(phones=_CH_COLLAPSED).find(text)] == [
        (text, "tel:+41794343254")
    ]


def test_collapse_whitespace_measures_a_run_as_it_renders() -> None:
    text = f"+41{' ' * 300}79434 3254"
    assert [(span.text, span.url) for span in LinkDetector(phones=_CH_COLLAPSED).find(text)] == [
        (text, "tel:+41794343254")
    ]
    assert _found(_CH, text) == []


def test_collapse_whitespace_reads_a_broken_extension() -> None:
    assert _found(_US_COLLAPSED, "650-253-0000\next. 1234") == ["tel:+16502530000;ext=1234"]
    assert _found(_US, "650-253-0000\next. 1234") == ["tel:+16502530000"]


def test_collapse_whitespace_reaches_an_identifier_label_over_a_run() -> None:
    assert _found(_US_COLLAPSED, "Order\f650-253-0000") == []
    assert _found(_US, "Order\f650-253-0000") == ["tel:+16502530000"]


def test_collapse_whitespace_keeps_a_timestamp_out() -> None:
    assert _found(_US_COLLAPSED, "20250901\n10:30") == []
    assert _found(_US_COLLAPSED, "20250901\n10") == ["tel:+12025090110"]


def test_collapse_whitespace_groups_across_a_run() -> None:
    strict = PhoneNumbers(regions=("US",), grouping=PhoneGrouping.EXACT, collapse_whitespace=True)
    assert _found(strict, "650\n253 0000") == ["tel:+16502530000"]
    assert _found(PhoneNumbers(regions=("US",), grouping=PhoneGrouping.EXACT), "650\n253 0000") == []


def test_collapse_whitespace_links_a_number_a_formatter_wrapped() -> None:
    assert linkify("<p>Call\n+41 79\n434 32 54\ntoday</p>", Linkify(phones=_CH_COLLAPSED)) == (
        '<p>Call\n<a href="tel:+41794343254">+41 79\n434 32 54</a>\ntoday</p>'
    )


def test_collapse_whitespace_survives_a_pickle_and_a_copy() -> None:
    restored = pickle.loads(pickle.dumps(_CH_COLLAPSED))  # ruff:ignore[suspicious-pickle-usage]  # this test's bytes
    assert restored == _CH_COLLAPSED
    assert copy.deepcopy(_CH_COLLAPSED) == _CH_COLLAPSED


# numbers, dates, an address, an extension and prose, the pieces a run of whitespace can end up gluing together
_PIECES: Final = (
    "+41", "79434", "3254", "079", "434", "32", "54", "650", "253", "0000", "(650)", "-", ".", "/", "x12", "Order",
    "Tel:", "abc", "+1", "20250901", "10:30", "4111", "1111", "pages", "1-5", "(3", "pages)", "2001:db8::8888", "020",
    "7946", "0958", "#", ",", "]", "ext.", "~", "[", "\u00a0", "\u3000",
)  # fmt: skip
_SEPARATORS: Final = (" ", "  ", "     ", "\n", "\t", "\r\n", " \n ", "\f", "")
_RUNS: Final = re.compile(r"[ \t\n\f\r]+")


def _corpus() -> list[str]:
    """Glue the pieces with every run of whitespace the knob has to read as one space."""
    rng = random.Random(20260901)  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # a fixed corpus
    return [
        "".join(rng.choice(_PIECES) + rng.choice(_SEPARATORS) for _ in range(rng.randint(2, 7))) for _ in range(20_000)
    ]


def test_collapse_whitespace_matches_the_text_as_it_renders() -> None:
    """The rule the knob promises: a run reads as the one space HTML paints, whatever it holds."""
    regions = ("CH", "US", "GB")
    collapsed = LinkDetector(phones=PhoneNumbers(regions=regions, collapse_whitespace=True))
    rendered = LinkDetector(phones=PhoneNumbers(regions=regions))
    for text in _corpus():
        assert [(span.url, _RUNS.sub(" ", span.text)) for span in collapsed.find(text)] == [
            (span.url, span.text) for span in rendered.find(_RUNS.sub(" ", text))
        ], text
