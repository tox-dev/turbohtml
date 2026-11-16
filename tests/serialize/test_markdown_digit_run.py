from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import pytest

from turbohtml import parse

if TYPE_CHECKING:
    from work_steps import WorkSteps


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>1<wbr>2<wbr>3</p>", "123", id="digits-without-marker"),
        pytest.param("<p>1<wbr>2.</p>", "12\\.", id="marker-after-empty-element"),
        pytest.param("<p>1<wbr>2)</p>", "12\\)", id="paren-marker-after-empty-element"),
        pytest.param("<p>1<wbr>x<wbr>2.</p>", "1x2.", id="letter-ends-the-run"),
        pytest.param("<p>1<wbr>.</p>", "1\\.", id="dot-after-empty-element"),
    ],
)
def test_digits_around_empty_elements_keep_their_markers(html: str, expected: str) -> None:
    assert parse(html).to_markdown() == expected


def test_long_digit_run_before_empty_elements_writes_every_digit() -> None:
    assert parse(_digits_then_empty(2000)).to_markdown() == f"{'1' * 2000}2\\."


def _digits_then_empty(count: int) -> str:
    return f"<p>{'1<wbr>' * count}2.</p>"


def test_digit_run_before_empty_elements_steps_grow_linearly(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda count: partial(parse(_digits_then_empty(count)).to_markdown), (1000, 4000))
