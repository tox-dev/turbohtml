"""
In-process fuzz worker for the CPython-coupled entry points.

The tokenizer, tree builder, serializer, sanitizer, URL parser, and HTML/CSS minifiers reach the live PyObject tree and
the CPython string API at more than one boundary, so -- unlike the JS minifier and the IDNA core -- they do not decouple
into a malloc-backed standalone target without rewriting production C the sanitize-unify refactor is actively touching.
This worker instead drives them through the public Python API against an extension compiled with
``-fsanitize=address,undefined`` (see ``tools/fuzz/fuzz.py``), so a C out-of-bounds access, use-after-free, or undefined
operation aborts the interpreter with a sanitizer stack trace. Calling the public API keeps every target robust to the
internal C refactors in flight (tox-dev/turbohtml#478).

``smoke`` replays the past finds and a benign seed corpus once per target: deterministic and expected to stay green, so
it gates every PR. ``deep`` adds a mutation loop and structural probes for a wall-clock budget per target. Each input is
written to a file named after its target in the repro directory before the call, so an abort leaves its crasher behind.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import importlib
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final

import turbohtml
from turbohtml import clean

# the harness targets the private URL/IDNA C bindings (th_url_split/join/percent, th_url_to_ascii) by design
from turbohtml._html import (  # ruff:ignore[import-private-name]
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
# Script text where "<!--" opens and "<script" plus a tag end follows before any "-->" breaks the script content
# restrictions (https://html.spec.whatwg.org/multipage/scripting.html#restrictions-for-contents-of-script-elements):
# reparsing its serialization lands in the double-escaped state, which swallows the closing "</script>". The standard
# warns that such parser-made trees need not survive serialize and reparse
# (https://html.spec.whatwg.org/multipage/parsing.html#serialising-html-fragments).
_SCRIPT_DOUBLE_ESCAPE: Final = re.compile(r"<!--(?:(?!-->).)*?<script[\t\n\f />]", re.IGNORECASE | re.DOTALL)
# TH_MAX_TREE_DEPTH (src/turbohtml/_c/dom/tree.h): past 512 open elements a start tag is inserted but not pushed, so
# further tags become siblings and reparsing the flattened output nests them differently
_MAX_TREE_DEPTH: Final = 512


def main() -> int:
    """Drive one or all targets; return 1 on any soft finding, 0 if clean (an ASan abort exits nonzero on its own)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "deep", "alloc"), required=True)
    parser.add_argument("--target", default="all")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--regression-dir", type=Path, required=True)
    parser.add_argument("--repro-dir", type=Path, required=True)
    parser.add_argument("--crash-dir", type=Path, required=True)
    parser.add_argument("--minutes", type=float, default=1.0)
    parser.add_argument("--rng-seed", type=int, default=0)
    parser.add_argument("--no-structural", action="store_true", help="skip the escalating-depth probes (a known DoS)")
    args = parser.parse_args()

    regressions = _inputs(args.regression_dir)
    findings: list[str] = []
    for name in (_ALLOC_TARGETS if args.mode == "alloc" else _TARGETS) if args.target == "all" else [args.target]:
        repro = args.repro_dir / name
        seeds = regressions + _inputs(args.corpus_dir / name)
        if args.mode == "alloc":
            findings += _sweep(name, seeds, repro, args.crash_dir)
        else:
            findings += [
                _finding(name, f"seed {index}", found, data, args.crash_dir)
                for index, data in enumerate(seeds)
                if (found := _run_one(_TARGETS[name], data, repro)) is not None
            ]
            if args.mode == "deep":
                findings += _hunt(name, seeds, repro, args)
            else:
                print(f"smoke {name}: {len(seeds)} seeds clean")
        repro.unlink()
    return 1 if findings else 0


def _sweep(name: str, seeds: list[bytes], repro: Path, crash_dir: Path) -> list[str]:
    """
    Fail every PyMem allocation of every seed in turn and require ``MemoryError`` exactly when the failure fires.

    libxml2's fuzzers inject failures the same way
    (https://github.com/GNOME/libxml2/blob/c43dc98d27ac315a48d93dbd399c6c22cf7125b1/fuzz/fuzz.c#L83-L181), and its
    ``malloc-fail`` fixes show where such bugs hide: cleanup paths. A ``failure_pos`` of 0 only counts the seed's N
    allocations; positions 1..N then fail one each. An ASan report aborts the process, and ``fuzz.py`` keeps the framed
    input it died on.
    """
    # only the fuzz-only build (meson -Dfuzzing=true) defines the hook, so the stubs omit it
    inject: Callable[[int], tuple[int, bool]] = vars(importlib.import_module("turbohtml._html"))["_fuzz_inject_failure"]
    func = _TARGETS[name]
    findings: list[str] = []
    swept = 0
    for index, seed in enumerate(seeds):
        found, allocations = _run_failing(inject, func, bytes(4) + seed, repro)
        for position in range(allocations + 1):
            data = position.to_bytes(4, "big") + seed
            if position:
                found, _ = _run_failing(inject, func, data, repro)
            if found is not None:
                findings.append(_finding(name, f"seed {index} failure_pos {position}", found, data, crash_dir))
        swept += allocations
    print(f"alloc {name}: {len(seeds)} seeds, {swept} failure positions swept")
    return findings


def _run_failing(
    inject: Callable[[int], tuple[int, bool]], func: Callable[[bytes], None], data: bytes, repro: Path
) -> tuple[str | None, int]:
    """
    Run ``func`` on the payload after a big-endian u32 ``failure_pos`` header and judge the outcome.

    libxml2's fuzzers read the same 4-byte field with ``xmlFuzzReadInt(4)``. The ``finally`` closes the injection window
    before any exception handling here can allocate. Returns the finding, or None, and the number of allocations the
    call made.
    """
    repro.write_bytes(data)
    error: Exception | None = None
    inject(int.from_bytes(data[:4], "big"))
    try:
        try:
            func(data[4:])
        finally:
            allocations, failed = inject(0)
    except (MemoryError, SystemError, RecursionError, AssertionError, *_EXPECTED) as exc:
        error = exc
    outcome = "a result" if error is None else f"{type(error).__name__}: {error}"
    if failed:
        return (
            None if error is None or isinstance(error, MemoryError) else f"{outcome} for an injected allocation failure"
        ), allocations
    if error is None or isinstance(error, _EXPECTED):
        return None, allocations
    return f"{outcome} without an injected allocation failure", allocations


def _inputs(directory: Path) -> list[bytes]:
    return [path.read_bytes() for path in sorted(directory.glob("*")) if path.is_file()]


def _run_one(func: Callable[[bytes], None], data: bytes, repro: Path) -> str | None:
    repro.write_bytes(data)
    try:
        func(data)
    except _EXPECTED:
        return None
    except RecursionError:
        return "RecursionError (soft DoS)"
    except AssertionError as exc:
        return f"invariant broken: {exc}"
    return None


# the default leaves <script>, <style> and style="" verbatim; the minifier's JS and CSS paths run only when enabled
_MINIFY: Final = turbohtml.Minify(minify_js=clean.JSMinify(), minify_css=clean.CSSMinify())
_PHONES: Final = clean.PhoneNumbers(regions=("US", "GB", "DE", "IN"))
_PHONE_DETECTOR: Final = clean.LinkDetector(phones=_PHONES)
_PHONE_LINKER: Final = clean.Linker(clean.Linkify(phones=_PHONES, parse_email=True))
_PHONE_POSSIBLE: Final = clean.LinkDetector(phones=clean.PhoneNumbers(regions=("US",), require_valid=False))
_PHONE_EXACT: Final = clean.LinkDetector(
    phones=clean.PhoneNumbers(regions=("US", "DE"), grouping=clean.PhoneGrouping.EXACT)
)


def _phone(data: bytes) -> None:
    text = _decode(data)
    for span in _PHONE_DETECTOR.find(text):
        if span.phone is not None:
            clean.PhoneNumber(*dataclasses.astuple(span.phone))
            for style in clean.PhoneFormat:
                span.phone.format(style)
    _PHONE_POSSIBLE.find(text)
    _PHONE_EXACT.find(text)
    _PHONE_LINKER.linkify(text)
    for held in (text, "tel:" + text):
        for require_valid in (True, False):
            with contextlib.suppress(ValueError):
                clean.PhoneNumber.parse(held, regions=("US", "DE"), require_valid=require_valid)


def _parse(data: bytes) -> None:
    turbohtml.parse(_decode(data))
    turbohtml.parse_fragment(_decode(data))


def _serialize(data: bytes) -> None:
    turbohtml.parse(_decode(data)).serialize()


def _roundtrip(data: bytes) -> None:
    document = turbohtml.parse(_decode(data))
    once = document.serialize()
    twice = turbohtml.parse(once).serialize()
    if once != twice and not _reparse_may_differ(document, once, twice):
        message = "serialize not idempotent"
        raise AssertionError(message)


def _sanitize(data: bytes) -> None:
    text = _decode(data)
    once = clean.sanitize(text)
    twice = clean.sanitize(once)
    if (
        once != twice
        and not _html_newline_normalization_only(once, twice)
        and not _past_depth_cap(turbohtml.parse_fragment(text))
    ):
        message = "sanitize not idempotent"
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
    clean.minify(_decode(data), _MINIFY)


_TARGETS: Final[dict[str, Callable[[bytes], None]]] = {
    "phone": _phone,
    "parse": _parse,
    "serialize": _serialize,
    "roundtrip": _roundtrip,
    "sanitize": _sanitize,
    "url": _url,
    "idna": _idna,
    "minify_css": _minify_css,
    "minify_html": _minify_html,
}
# minify_html stays out because a failed text copy in its script and style minifiers crashes, and sanitize because
# sweeping its deep-nesting seed grows past 7 GiB
_ALLOC_TARGETS: Final[tuple[str, ...]] = ("phone", "parse", "serialize", "roundtrip", "url", "idna", "minify_css")


def _decode(data: bytes) -> str:
    """Widen raw fuzz bytes to a str, keeping lone surrogates so the IDNA surrogate path is reachable."""
    try:
        return data.decode("utf-8", "surrogatepass")
    except UnicodeError:
        return data.decode("latin-1")


def _reparse_may_differ(document: turbohtml.Document, once: str, twice: str) -> bool:
    return (
        _html_newline_normalization_only(once, twice)
        or _past_depth_cap(document)
        or any(_SCRIPT_DOUBLE_ESCAPE.search(script.text) for script in document.iter_elements("script"))
    )


def _html_newline_normalization_only(once: str, twice: str) -> bool:
    # HTML serialization leaves CR literal, while parsing it normalizes CR and CRLF to LF.
    return once.replace("\r\n", "\n").replace("\r", "\n") == twice


def _past_depth_cap(root: turbohtml.Node) -> bool:
    # the root node plus 512 ancestors is the deepest placement the tree builder allows, reached only on a full stack
    return any(sum(1 for _ in element.ancestors) > _MAX_TREE_DEPTH for element in root.iter_elements())


def _finding(target: str, origin: str, kind: str, data: bytes, crash_dir: Path) -> str:
    """
    Keep the input for the encrypted upload and log it by hash.

    A public CI log must carry neither the crasher bytes nor the origin (seed and mutation index) that regenerates
    them, so the origin goes to the encrypted ``.replay`` file. It logs at once, not at exit, so a later sanitizer abort
    in the same run cannot swallow the line.
    """
    digest = hashlib.sha256(data).hexdigest()
    (crash_dir / f"crash-{digest}").write_bytes(data)
    (crash_dir / f"crash-{digest}.replay").write_text(f"{origin} PYTHONMALLOC={os.environ.get('PYTHONMALLOC')}\n")
    finding = f"[{target}] {kind} sha256={digest} bytes={len(data)}"
    print(f"FINDING {finding}", file=sys.stderr, flush=True)
    return finding


def _hunt(name: str, seeds: list[bytes], repro: Path, args: argparse.Namespace) -> list[str]:
    func = _TARGETS[name]
    findings = [
        _finding(name, f"structural {index}", found, data, args.crash_dir)
        for index, data in enumerate(() if args.no_structural else _structural(name))
        if (found := _run_one(func, data, repro)) is not None
    ]
    rng = random.Random(args.rng_seed)
    deadline = time.monotonic() + args.minutes * 60
    runs = 0
    while time.monotonic() < deadline:
        if (found := _run_one(func, data := _mutate(rng, rng.choice(seeds)), repro)) is not None:
            findings.append(_finding(name, f"mutation {runs} of rng-seed {args.rng_seed}", found, data, args.crash_dir))
        runs += 1
    print(f"deep {name}: {runs} mutations + structural probes done")
    return findings


def _structural(target: str) -> Iterator[bytes]:
    if target in {"parse", "serialize", "roundtrip", "sanitize", "minify_html"}:
        # deep enough that the recursive sanitizer walk overflows (around 8k frames under ASan), shallow enough that the
        # quadratic parse stays bounded, so the deep run reaches the stack-overflow class without a multi-minute parse
        for depth in (256, 2048, 16384, 32768):
            yield b"<div>" * depth
            yield b"<a>" * depth
            yield (b"<b>" * depth) + (b"</b>" * depth)
    if target in {"url", "idna"}:
        yield b"xn--" + b"a" * 20000
        yield b"a." * 20000


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


def _mutate(rng: random.Random, seed: bytes) -> bytes:
    out = bytearray(seed)
    for _ in range(rng.randint(1, 8)):
        if not out or rng.random() < 0.3:
            out[rng.randint(0, len(out)) if out else 0 : 0] = rng.choice(_INTERESTING)
        elif rng.random() < 0.5:
            index = rng.randrange(len(out))
            out[index] ^= 1 << rng.randrange(8)
        else:
            del out[rng.randrange(len(out))]
    return bytes(out)


if __name__ == "__main__":
    raise SystemExit(main())
