"""The conformance checker's C entry points: the verdict it returns and the severity filter's contract."""

from __future__ import annotations

from typing import NamedTuple

import pytest

from turbohtml import parse
from turbohtml._html import _conformance_check, _conformance_filter
from turbohtml.conformance import ConformanceMessage


def test_check_returns_the_verdict_with_the_findings() -> None:
    valid, findings = _conformance_check(parse("<title>t</title><html lang=en><img>"))
    assert (valid, [finding[0] for finding in findings]) == (False, ["img-missing-alt"])


def test_check_is_valid_with_only_a_warning() -> None:
    valid, findings = _conformance_check(parse("<title>t</title><html lang=en><section><p>x</p></section>"))
    assert (valid, [finding[1] for finding in findings]) == (True, ["warning"])


_ERROR = ConformanceMessage("img-missing-alt", "error", "m", 1, 0)
_WARNING = ConformanceMessage("missing-lang", "warning", "m", 1, 0)


def test_the_filter_keeps_only_the_named_severity_in_order() -> None:
    assert _conformance_filter((_WARNING, _ERROR, _WARNING), "warning") == (_WARNING, _WARNING)


class _Unlabelled(NamedTuple):
    """A record whose severity is not a string, so it can never equal one."""

    severity: int


def test_the_filter_skips_a_message_whose_severity_is_not_a_str() -> None:
    assert _conformance_filter((_Unlabelled(1), _ERROR), "error") == (_ERROR,)  # ty: ignore[invalid-argument-type]


def test_the_filter_needs_a_severity_on_every_message() -> None:
    with pytest.raises(AttributeError):
        _conformance_filter((object(),), "error")  # ty: ignore[invalid-argument-type]  # the attribute read is the point


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(([_ERROR], "error"), id="messages-is-a-list"),
        pytest.param(((_ERROR,), 1), id="severity-is-not-a-str"),
    ],
)
def test_the_filter_rejects_bad_arguments(args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        _conformance_filter(*args)  # ty: ignore[invalid-argument-type]  # the argument check is the point
