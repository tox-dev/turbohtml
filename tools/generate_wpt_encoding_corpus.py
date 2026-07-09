"""
Generate tests/conformance/data/wpt_encoding.json from a web-platform-tests checkout.

WPT is the browsers' own encoding suite, but its git history runs to 2.6 GB, so it is refreshed into a committed corpus
rather than vendored as a submodule the way ``encoding_rs`` and ``chardetng`` are. Rerun this against a fresh checkout
whenever you want the corpus to track wpt HEAD; the commit it was taken from is recorded in the corpus.

Two of wpt's encoding oracles are usable outside a browser:

``encoding/resources/single-byte-decoder.js``
    the 128 code points each legacy single-byte encoding maps its high bytes to, as a JSON-shaped JS literal.

``encoding/iso-2022-jp-decoder.any.js``
    explicit (byte sequence, expected string) pairs covering every state and error arm of the stateful decoder.

Its ``legacy-mb-*/**/*_chars.html`` files look like a third, and are not: the ``data-cp`` attribute on each span is a
label carried over from the W3C i18n suite that records the character in the *legacy* charset (JIS X 0201 conventions:
``0x5C`` is U+00A5, ``A1 DD`` is U+2212), while the test itself asserts against a JS reference decoder. Reading them as
expectations would pin the wrong values, so they are skipped.

Usage:  python tools/generate_wpt_encoding_corpus.py <wpt-checkout> tests/conformance/data/wpt_encoding.json

Get a checkout without paying for the history::

    git clone --depth 1 --filter=blob:none --sparse https://github.com/web-platform-tests/wpt
    cd wpt && git sparse-checkout set encoding
"""

from __future__ import annotations

import json
import re
import subprocess  # reads the pinned revision out of the caller's own wpt checkout
import sys
from pathlib import Path
from typing import Final

_SINGLE_BYTE_TABLE: Final = re.compile(r'"([^"]+)":\[([^\]]*)\]')
_ISO_2022_JP_CASE: Final = re.compile(r'decode\(\[([^\]]*)\],\s*("(?:[^"\\]|\\.)*")\s*,\s*"([^"]*)"\)')


def _revision(checkout: Path) -> str:
    """Return the commit the corpus is taken from, so a stale corpus names the wpt it came from."""
    result = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _single_byte(checkout: Path) -> list[dict[str, object]]:
    """One case per (encoding, high byte): the code point wpt says it decodes to, or null for an error."""
    source = (checkout / "encoding" / "resources" / "single-byte-decoder.js").read_text(encoding="utf-8")
    cases: list[dict[str, object]] = []
    for name, body in _SINGLE_BYTE_TABLE.findall(source):
        points = [None if value.strip() == "null" else int(value) for value in body.split(",")]
        if len(points) != 128:  # the file also carries the encodings_table import, whose shape differs
            continue
        cases.extend(
            {"encoding": name, "bytes": f"{0x80 + offset:02x}", "text": None if point is None else chr(point)}
            for offset, point in enumerate(points)
        )
    if not cases:
        msg = "no single-byte tables found; wpt moved resources/single-byte-decoder.js"
        raise SystemExit(msg)
    return cases


def _iso_2022_jp(checkout: Path) -> list[dict[str, object]]:
    """One case per decode() call in wpt's ISO-2022-JP suite, with its expected string and the arm it names."""
    source = (checkout / "encoding" / "iso-2022-jp-decoder.any.js").read_text(encoding="utf-8")
    cases = [
        {
            "encoding": "iso-2022-jp",
            "bytes": bytes(int(byte, 0) for byte in raw.split(",") if byte.strip()).hex(),
            "text": json.loads(expected.replace("\\x", "\\u00")),
            "arm": arm,
        }
        for raw, expected, arm in _ISO_2022_JP_CASE.findall(source)
    ]
    if not cases:
        msg = "no decode() cases found; wpt moved iso-2022-jp-decoder.any.js"
        raise SystemExit(msg)
    return cases


def generate(checkout: Path, out_path: Path) -> None:
    """Write the corpus, recording the wpt revision it came from."""
    cases = [*_single_byte(checkout), *_iso_2022_jp(checkout)]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "source": "https://github.com/web-platform-tests/wpt",
                "revision": _revision(checkout),
                "files": ["encoding/resources/single-byte-decoder.js", "encoding/iso-2022-jp-decoder.any.js"],
                "cases": cases,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out_path}: {len(cases)} cases from wpt {_revision(checkout)[:12]}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        msg = "usage: generate_wpt_encoding_corpus.py WPT_CHECKOUT OUTPUT_JSON"
        raise SystemExit(msg)
    generate(Path(sys.argv[1]), Path(sys.argv[2]))
