from __future__ import annotations

import pytest

from turbohtml._html import _registrable_domain


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        pytest.param("россия.рф", "россия.рф", id="cyrillic-unknown-suffix"),
        pytest.param("例え.jp", "例え.jp", id="cjk-label-ascii-suffix"),
        pytest.param("рф", "рф", id="bare-cyrillic-label"),
    ],
)
def test_registrable_domain_handles_a_raw_non_ascii_host(host: str, expected: str) -> None:
    # The raw binding skips the public URL normalizer's IDNA conversion.
    assert _registrable_domain(host) == expected
