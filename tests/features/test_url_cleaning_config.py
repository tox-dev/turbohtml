from __future__ import annotations

import pytest

from turbohtml.extract import UrlCleaning


def test_defaults_reproduce_no_argument_behaviour() -> None:
    options = UrlCleaning()
    assert (options.strict, options.trailing_slash, options.strip_fragment, options.strip_trackers) == (
        False,
        True,
        False,
        True,
    )


def test_aggressive_preset_is_the_strongest_canonicalization() -> None:
    options = UrlCleaning.aggressive()
    assert options.strict
    assert not options.trailing_slash
    assert options.strip_fragment


def test_keeping_trackers_under_strict_is_rejected() -> None:
    with pytest.raises(ValueError, match="strip_trackers=False is meaningless with strict=True"):
        UrlCleaning(strict=True, strip_trackers=False)
