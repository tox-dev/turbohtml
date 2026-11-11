from __future__ import annotations

from functools import partial
from typing import Final

import pytest

from turbohtml import parse
from turbohtml._html import url_clean as _url_clean
from turbohtml._html import url_normalize as _url_normalize

_KNOBS: Final[tuple[bool | frozenset[str] | None, ...]] = (
    False,
    True,
    False,
    None,
    frozenset(),
    frozenset({"id"}),
    frozenset({"lang"}),
)


@pytest.mark.parametrize(
    ("entry", "args"),
    [
        pytest.param(partial(_url_normalize), ("http://x.com", *_KNOBS[:-1]), id="normalize-too-few"),
        pytest.param(partial(_url_clean), ("http://x.com", *_KNOBS), id="clean-too-few"),
        pytest.param(partial(_url_normalize), ("http://x.com", *_KNOBS[:3], 5, *_KNOBS[4:]), id="allow-not-iterable"),
        pytest.param(partial(_url_normalize), ("http://x.com", *_KNOBS[:3], {1}, *_KNOBS[4:]), id="allow-holds-an-int"),
        pytest.param(partial(_url_normalize), ("http://x.com", *_KNOBS[:4], {1}, *_KNOBS[5:]), id="deny-holds-an-int"),
        pytest.param(
            partial(_url_clean),
            ("http://x.com", *_KNOBS[:3], {1}, *_KNOBS[4:], None, frozenset()),
            id="clean-allow-holds-an-int",
        ),
    ],
)
def test_the_entry_points_reject_bad_arguments(
    entry: partial[str], args: tuple[str | bool | int | set[int] | frozenset[str] | None, ...]
) -> None:
    with pytest.raises(TypeError):
        entry(*args)


def test_the_document_entry_point_rejects_too_few_arguments() -> None:
    external_only = False
    with pytest.raises(TypeError):
        parse("")._extract_links(None, external_only)  # ty: ignore[missing-argument]  # tests the C argument guard
