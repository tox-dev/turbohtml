"""
Validate turbohtml's link detector against linkify-it's own fixture corpus.

linkify-it (https://github.com/markdown-it/linkify-it) is the reference plain-text link scanner: markdown-it uses it,
linkify-it-py is a line-for-line port of it, and its ``test/fixtures`` pair is the corpus every regression it ever fixed
lives in. It is vendored as a pinned shallow submodule at ``tests/conformance/linkify-it``; ``links.txt`` holds inputs
that must produce a link, optionally followed by the exact match expected, and ``not_links.txt`` holds inputs that must
produce none. The parser here mirrors linkify-it's own runner, trailing ``%`` notes included, so the corpus is read the
way upstream reads it -- a line is its own expectation unless the next line supplies one.

turbohtml is not a port of linkify-it and does not aim for byte parity: it detects a narrower, less fuzzy set on
purpose. So every case is asserted, and the cases where the two disagree are listed in ``_DEVIATIONS`` with the reason
and with turbohtml's own answer, which is asserted just as strictly. A deviation that silently changes fails here; one
that gets fixed fails here too, and its entry is deleted.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from turbohtml.clean import LinkDetector

_SUBMODULE: Final = Path(__file__).parent / "linkify-it"
_FIXTURES: Final = _SUBMODULE / "test" / "fixtures"

# The corpus is a vendored data submodule: the dedicated "conformance" CI job checks it out, so its absence is a setup
# error, not a reason to skip -- a silent skip would let the whole suite vanish.
if not _FIXTURES.exists():  # pragma: no cover
    msg = (
        "submodule tests/conformance/linkify-it not checked out; "
        "run: git submodule update --init tests/conformance/linkify-it"
    )
    raise RuntimeError(msg)

# Where turbohtml deliberately answers differently, keyed by the fixture line, valued as (turbohtml's first match or
# None, why). Each reason is a documented design boundary, not an unfixed defect.
_DEVIATIONS: Final = {
    "//localhost": (None, "a dotless authority needs its scheme written"),
    "//test.123": (None, "a numeric top-level label needs its scheme written"),
    "//[2001:db8:0:0:0:0:2:1]/abc": (None, "an IP literal needs its scheme written"),
    "My ssl //example.com site": ("example.com", "the span drops the protocol-relative //, the href fills in http://"),
    "4.4.4.4": (None, "a bare IP address is a fuzzy match turbohtml does not make"),
    "192.168.1.1/abc": (None, "a bare IP address is a fuzzy match turbohtml does not make"),
    "test.example@http://vk.com": (None, "the address matcher claims the run before the scheme matcher sees it"),
    'http://foo.com/blah_blah_"doublequoted"': (
        "http://foo.com/blah_blah_",
        "a double quote ends a URL, so a quoted path is trimmed",
    ),
    "http://foo.com/blah_blah_'singlequoted'": (
        "http://foo.com/blah_blah_'singlequoted",
        "a trailing single quote is a closing delimiter, never the last byte of a URL",
    ),
    "mailto:foo@bar      % explicit protocol make it valid": (
        None,
        "an address needs a host with a known top-level domain, scheme or not",
    ),
    "mailto:user@[IPv6:::1]": (None, "an address host is a domain, never an IP literal"),
    "mailto:user@[IPv6:2001:db8::1]": (None, "an address host is a domain, never an IP literal"),
    "\uff5cwww.google.com/www.google.com/foo\uff5cbar    % #46, asian vertical pipes": (
        "\uff5cwww.google.com/www.google.com/foo\uff5cbar",
        "the fullwidth vertical line is not a text separator",
    ),
    "\uff5ctest@google.com\uff5cbar": (None, "the fullwidth vertical line is not a text separator"),
    "\uff5chttp://google.com\uff5cbar": (None, "the fullwidth vertical line is not a text separator"),
    "_//example.com": ("example.com", "only a scheme colon disqualifies a protocol-relative //"),
    "http://example.com_": ("http://example.com_", "an explicit scheme means the author vouched for the host"),
    "google.com:500000 // invalid port": ("google.com", "an out-of-range port is trimmed and the host still links"),
    "/path/to/file.pl": ("file.pl", "a slash with no scheme colon does not disqualify a bare domain"),
    "http://[2001:db8:::1]/": ("http://[2001:db8:::1]/", "the brackets bound the literal; the address is not parsed"),
    "http://[1:2:3:4:5:6:7:8:9]/": (
        "http://[1:2:3:4:5:6:7:8:9]/",
        "the brackets bound the literal; the address is not parsed",
    ),
}


def _lines(name: str) -> list[str]:
    """Return the fixture's lines with whole-line ``%`` notes blanked, the way linkify-it's own runner reads them."""
    return [re.sub(r"^%.*", "", line) for line in (_FIXTURES / name).read_text(encoding="utf-8").split("\n")]


def _link_cases() -> list[tuple[str, str]]:
    """Return each ``links.txt`` input with the match it must produce: the next line when there is one, else itself."""
    lines = _lines("links.txt")
    cases: list[tuple[str, str]] = []
    skip = False
    for number, line in enumerate(lines):
        following = lines[number + 1] if number + 1 < len(lines) else ""
        if skip or not line.strip():
            skip = False
            continue
        cases.append((line, following if following.strip() else line))
        skip = bool(following.strip())
    return cases


def _first(text: str) -> str | None:
    """Return the text of the first link turbohtml finds, or None."""
    spans = LinkDetector().find(text)
    return spans[0].text if spans else None


_LINKS: Final = _link_cases()
_NOT_LINKS: Final = [line for line in _lines("not_links.txt") if line.strip()]


@pytest.mark.parametrize(("text", "expected"), [pytest.param(*case, id=case[0]) for case in _LINKS])
def test_linkifyit_links_fixture(text: str, expected: str) -> None:
    if (deviation := _DEVIATIONS.get(text)) is not None:
        assert _first(text) == deviation[0], deviation[1]
        return
    assert _first(text) == expected


@pytest.mark.parametrize("text", [pytest.param(line, id=line) for line in _NOT_LINKS])
def test_linkifyit_not_links_fixture(text: str) -> None:
    if (deviation := _DEVIATIONS.get(text)) is not None:
        assert _first(text) == deviation[0], deviation[1]
        return
    assert _first(text) is None


def test_every_deviation_is_still_a_deviation() -> None:
    # a listed case that upstream no longer covers is a stale entry the corpus bump should have removed
    covered = {text for text, _ in _LINKS} | set(_NOT_LINKS)
    assert set(_DEVIATIONS) <= covered
