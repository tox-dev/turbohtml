"""The Matching config: soupsieve's namespaces/flags bundled into one frozen object."""

from __future__ import annotations

import pytest

from turbohtml.match import DEBUG, Matching


def test_default_is_soupsieves_html_mode() -> None:
    assert Matching() == Matching(namespaces=None, flags=0)


def test_config_is_frozen() -> None:
    with pytest.raises(AttributeError):
        Matching().flags = 1  # ty: ignore[invalid-assignment]  # asserting the frozen dataclass rejects it


def test_soupsieve_preset_maps_the_call_convention() -> None:
    namespaces = {"svg": "http://www.w3.org/2000/svg"}
    config = Matching.soupsieve(namespaces=namespaces, flags=DEBUG)
    assert config == Matching(namespaces=namespaces, flags=DEBUG)


def test_soupsieve_preset_defaults_match_the_plain_config() -> None:
    assert Matching.soupsieve() == Matching()


def test_debug_flag_value() -> None:
    assert DEBUG == 0x1
