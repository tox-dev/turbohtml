"""
In-process fuzz worker for the CPython-coupled entry points.

The tokenizer, tree builder, serializer, sanitizer, URL parser, and HTML/CSS minifiers reach the live PyObject tree and
the CPython string API at more than one boundary, so -- unlike the JS minifier and the IDNA core -- they do not decouple
into a malloc-backed standalone target without rewriting production C the sanitize-unify refactor is actively touching.
This worker instead drives them through the public Python API against an extension compiled with
``-fsanitize=address,undefined`` (see ``tools/fuzz/fuzz.py``), so a C out-of-bounds access, use-after-free, or undefined
operation aborts the interpreter with a sanitizer stack trace. Calling the public API keeps every target robust to the
internal C refactors in flight (tox-dev/turbohtml#478).

``smoke`` replays a benign seed corpus once per target: fast, deterministic, and expected to stay green, so it gates
every PR. ``deep`` adds a mutation loop and structural probes (escalating nesting depth, which the research flags as the
most likely stack-overflow finding) for a wall-clock budget per target, for the scheduled/manual deep run. Before each
call the current input is written to the repro file, so an abort names its own crasher.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final

import turbohtml
from turbohtml import clean

# the harness targets the private URL/IDNA C bindings (th_url_split/join/percent, th_url_to_ascii) by design
from turbohtml._urls import (  # noqa: PLC2701
    _url_join,
    _url_percent_decode,
    _url_percent_encode,
    _url_split,
    _url_to_ascii,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# A malformed input is expected to raise, not crash; only a sanitizer abort (which kills the process) or an unexpected
# exception type is a finding. RecursionError from a deep Python walk is surfaced separately as a soft DoS signal.
_EXPECTED: Final = (ValueError, UnicodeError, turbohtml.HTMLParseError)
_SET_IDS: Final = (0, 1, 2)


def _decode(data: bytes) -> str:
    """Widen raw fuzz bytes to a str, keeping lone surrogates so the IDNA surrogate path is reachable."""
    try:
        return data.decode("utf-8", "surrogatepass")
    except UnicodeError:
        return data.decode("latin-1")


def _parse(data: bytes) -> None:
    turbohtml.parse(_decode(data))
    turbohtml.parse_fragment(_decode(data))


def _serialize(data: bytes) -> None:
    turbohtml.parse(_decode(data)).serialize()


def _roundtrip(data: bytes) -> None:
    once = turbohtml.parse(_decode(data)).serialize()
    twice = turbohtml.parse(once).serialize()
    if once != twice:
        message = f"serialize not idempotent: {once!r} != {twice!r}"
        raise AssertionError(message)


def _sanitize(data: bytes) -> None:
    once = clean.sanitize(_decode(data))
    if clean.sanitize(once) != once:
        message = f"sanitize not idempotent for {_decode(data)!r}"
        raise AssertionError(message)


def _url(data: bytes) -> None:
    text = _decode(data)
    _url_split(text)
    _url_percent_decode(text)
    for set_id in _SET_IDS:
        _url_percent_encode(text, set_id)
    _url_join("https://a.example/b/c?d#e", text)
    _url_join(text, "https://a.example/b/c?d#e")
    clean.linkify(text)


def _idna(data: bytes) -> None:
    _url_to_ascii(_decode(data))


def _minify_css(data: bytes) -> None:
    clean.minify_css(_decode(data))


def _minify_html(data: bytes) -> None:
    clean.minify(_decode(data))


TARGETS: Final[dict[str, Callable[[bytes], None]]] = {
    "parse": _parse,
    "serialize": _serialize,
    "roundtrip": _roundtrip,
    "sanitize": _sanitize,
    "url": _url,
    "idna": _idna,
    "minify_css": _minify_css,
    "minify_html": _minify_html,
}

_INTERESTING: Final[tuple[bytes, ...]] = (
    b"<script>",
    b"</script>",
    b"<!--",
    b"-->",
    b"<![CDATA[",
    b"]]>",
    b"<math>",
    b"<svg>",
    b"<mtext>",
    b"<annotation-xml>",
    b"<style>",
    b"onerror=",
    b"javascript:",
    b"&#x9;",
    b"xn--",
    b"\\@",
    b"://",
    b"%ff",
    b'"',
    b"'",
    b"\x00",
    b"..",
)


def _structural(target: str) -> Iterator[bytes]:
    """
    Yield escalating-depth probes for the recursive walks.

    Depths cap where the quadratic parse cost stays bounded yet the recursive sanitizer walk still overflows (it faults
    around 8k frames under ASan), so the deep run surfaces the stack-overflow class without a multi-minute parse.
    """
    if target in {"parse", "serialize", "roundtrip", "sanitize", "minify_html"}:
        for depth in (256, 2048, 16384, 32768):
            yield b"<div>" * depth
            yield b"<a>" * depth
            yield (b"<b>" * depth) + (b"</b>" * depth)
    if target in {"url", "idna"}:
        yield b"xn--" + b"a" * 20000
        yield b"a." * 20000


def _mutate(rng: random.Random, seed: bytes) -> bytes:
    out = bytearray(seed)
    for _ in range(rng.randint(1, 8)):
        if not out or rng.random() < 0.3:
            out[rng.randint(0, len(out)) if out else 0 : 0] = rng.choice(_INTERESTING)
        elif rng.random() < 0.5:
            index = rng.randrange(len(out))
            out[index] ^= 1 << rng.randrange(8)
        else:
            index = rng.randrange(len(out))
            del out[index]
    return bytes(out)


def _run_one(func: Callable[[bytes], None], data: bytes, repro: Path) -> str | None:
    repro.write_bytes(data)
    try:
        func(data)
    except _EXPECTED:
        return None
    except RecursionError:
        return f"RecursionError (soft DoS) on {data[:80]!r}"
    except AssertionError as exc:
        return f"invariant broken: {exc}"
    return None


def _corpus(corpus_dir: Path, target: str) -> list[bytes]:
    return [path.read_bytes() for path in sorted((corpus_dir / target).glob("*")) if path.is_file()]


def main() -> int:
    """Drive one or all targets; return 1 on any soft finding, 0 if clean (an ASan abort exits nonzero on its own)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "deep"), required=True)
    parser.add_argument("--target", default="all")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--repro", type=Path, required=True)
    parser.add_argument("--minutes", type=float, default=1.0)
    parser.add_argument("--rng-seed", type=int, default=0)
    parser.add_argument("--no-structural", action="store_true", help="skip the escalating-depth probes (a known DoS)")
    args = parser.parse_args()

    names = list(TARGETS) if args.target == "all" else [args.target]
    findings: list[str] = []
    for name in names:
        func = TARGETS[name]
        seeds = _corpus(args.corpus_dir, name) or [b""]
        findings += [f"[{name}] {found}" for data in seeds if (found := _run_one(func, data, args.repro)) is not None]
        if args.mode == "smoke":
            print(f"smoke {name}: {len(seeds)} seeds clean")
            continue
        probes = () if args.no_structural else _structural(name)
        findings += [
            f"[{name}] structural {f}" for data in probes if (f := _run_one(func, data, args.repro)) is not None
        ]
        rng = random.Random(args.rng_seed)
        deadline = time.monotonic() + args.minutes * 60
        runs = 0
        while time.monotonic() < deadline:
            if (found := _run_one(func, _mutate(rng, rng.choice(seeds)), args.repro)) is not None:
                findings.append(f"[{name}] {found}")
            runs += 1
        print(f"deep {name}: {runs} mutations + structural probes done")

    for finding in findings:
        print(f"FINDING {finding}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
