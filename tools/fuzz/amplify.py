"""
Super-linear running time and output growth lane (tox-dev/turbohtml#1009).

A crash fuzzer with a short deadline rarely sees a quadratic: the slope only shows on long inputs, and a fixed deadline
flags a slow runner as often as slow code. This lane grows an input instead and scores how thread CPU time scales with
its size.

A shape is ``head + left*N + middle + right*N + tail`` (``repeat`` leaves out the middle and right, ``sandwich`` keeps
both) or the ``tree`` interleave ``middle + left + middle + left*2 + middle ...``, each piece at most 128 bytes. It
renders at 1 KiB and doubles three times; a target is super-linear when its time grows 2.5x or more at every doubling,
and stays so while retests pool more samples. Targets that emit text also carry a ``len(out) <= k * len(in) + c`` bound.

``regressions`` re-scores the committed list of past super-linear inputs (``amplify_regressions.json``): fast, public,
and the per-PR gate. ``search`` draws shapes from literals harvested out of the C sources for a wall-clock budget or a
fixed ``--shapes`` count, in a worker process so a hang or a crash still leaves its input behind.

A finding lands in ``--crash-dir`` as ``crash-<sha256>.json`` and the log names it only by target, shape kind and hash,
because CI logs on a public repository are public; the seed stays out of the log too, since with the public code it
regenerates every finding. Both modes first score a pure-Python quadratic and a linear control, so a runner too noisy to
tell them apart fails the run instead of passing it.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import faulthandler
import gc
import hashlib
import itertools
import json
import math
import multiprocessing
import os
import random
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from functools import cache
from multiprocessing.sharedctypes import RawValue
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias

from turbohtml import Document, HTMLParseError, parse, parse_xml, tokenize
from turbohtml.clean import linkify, minify_css, minify_js, sanitize
from turbohtml.extract import clean_url, normalize_url
from turbohtml.transform import Transform
from turbohtml.validate import RelaxNG, XMLSchema

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence
    from ctypes import c_int
    from multiprocessing.process import BaseProcess

_JSON: TypeAlias = "dict[str, _JSON] | list[_JSON] | tuple[_JSON, ...] | str | int | float | bool | None"

_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_C_SOURCES: Final[Path] = _ROOT / "src" / "turbohtml" / "_c"
_REGRESSIONS: Final[Path] = Path(__file__).with_name("amplify_regressions.json")
_INFLIGHT: Final[str] = "inflight.json"
_SPAWN_PROCESS: Final[type[BaseProcess]] = multiprocessing.get_context("spawn").Process
_KINDS: Final[tuple[str, ...]] = ("repeat", "sandwich", "tree")
_COUNTER: Final[str] = "{n}"
# comrak quadratic.rs and pulldown-cmark's dos-fuzzer both cap each pattern string at 128 bytes
_MAX_PIECE: Final[int] = 128
_BASE_BYTES: Final[int] = 1024
# The C code caps work that would otherwise grow quadratically: TH_MAX_TREE_DEPTH open elements (512), TH_IDNA_MAX_INPUT
# code points (16,384), CSS_MAX_MERGE_REACH merged rules (256). A 1 KiB to 8 KiB run can sit inside such a cap, so a
# search hit is re-scored from 128 KiB, past 512 pieces of 128 bytes and 16,384 code points of 4 bytes; a slope gone
# by then is a cap, counted but not reported.
_PAST_CAPS: Final[int] = 128 * 1024
# Doubling the input of a linear target doubles its time (growth 2) and of a quadratic one quadruples it (4). comrak
# (https://github.com/kivikakk/comrak/blob/c8a2589fd3c085017724600c4e264639300ae905/fuzz/fuzz_targets/quadratic.rs#L322-L337)
# applies its 2.5 to the time per byte, where a quadratic scores 2 and passes, so the 2.5 here applies to the time.
_MIN_GROWTH: Final[float] = 2.5
# pulldown-cmark's TEST_COUNT: a hit has to survive five measurements
# (https://github.com/pulldown-cmark/pulldown-cmark/blob/c61583e33f043e926a5cbd4423252c6cb97301d2/dos-fuzzer/src/main.rs#L374-L398)
_RETESTS: Final[int] = 5
# Under a load average of 45 on 10 cores, ten measurements each of the quadratic and linear controls with interleaved
# 10 ms samples kept every doubling of the quadratic at 3.21 or more and of the linear at 2.31 or less; with 5 ms
# samples the quadratic fell to 2.09 and the linear rose to 3.66.
_MIN_SAMPLE_SECONDS: Final[float] = 0.010
# pulldown-cmark's MAX_MILLIS: a call this slow ends the measurement, its time standing as the last size's sample. Its
# watchdog fires at ten times that; a size here runs up to four such calls and the watchdog counts wall time, which a
# loaded runner stretches, so it waits 30 s.
_TOO_LONG_SECONDS: Final[float] = 0.5
# RecursionError is how the tree consumers refuse nesting past their depth limit (dom/tree.h th_node_check_max_depth);
# depth is the structural probes' concern in _targets.py, and the time spent refusing is still measured here
_EXPECTED: Final[tuple[type[Exception], ...]] = (ValueError, HTMLParseError, RecursionError)
# the descriptor, not sys.stderr: pytest and the search worker both swap the object, the watchdog needs the file
_STDERR: Final[int] = 2


def main(argv: Sequence[str] | None = None, process: _ProcessFactory = _SPAWN_PROCESS) -> int:
    # Return 0 when every scored shape grows linearly within its output bound, 1 on a finding, 2 on a blind run.
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    modes = parser.add_subparsers(dest="mode", required=True)
    regressions = modes.add_parser("regressions", help="re-score the committed super-linear regression list")
    regressions.add_argument("--file", type=Path, default=_REGRESSIONS, help="the regression list (_JSON)")
    search = modes.add_parser("search", help="score shapes drawn from the harvested C literals for a budget")
    search.add_argument("--minutes", type=float, default=1.0, help="wall-clock budget of the search")
    search.add_argument(
        "--shapes", type=_shape_count, help="stop after this many shapes instead of the wall-clock budget"
    )
    search.add_argument(
        "--rng-seed",
        type=int,
        default=int(os.environ.get("FUZZ_RNG_SEED", "0")),
        help="seed of the shape sequence (default: $FUZZ_RNG_SEED, else 0)",
    )
    search.add_argument("--crash-dir", type=Path, default=_ROOT / ".fuzz-crashes", help="where findings land")
    for mode in (regressions, search):
        mode.add_argument("--target", action="append", choices=sorted(TARGETS), help="limit to this target")
    args = parser.parse_args(argv)
    if blind := _control_failure():
        print(f"BLIND SCORER: {blind}", file=sys.stderr)
        return 2
    if args.mode == "regressions":
        return _rescore(load_regressions(args.file), args.target)
    budget = _Budget(
        seconds=math.inf if args.shapes is not None else args.minutes * 60,
        shapes=args.shapes,
        drawn=RawValue("i", 0),
    )
    return _search(args.target or sorted(TARGETS), budget, args.rng_seed, args.crash_dir, process)


class _ProcessFactory(Protocol):
    def __call__(
        self, *, target: Callable[[Sequence[str], _Budget, str, Path], None], args: tuple[object, ...]
    ) -> BaseProcess: ...


def _shape_count(text: str) -> int:
    count = int(text)
    if count < 1:
        msg = f"must be at least 1, got {count}"
        raise argparse.ArgumentTypeError(msg)
    return count


def _control_failure() -> str | None:
    control = Shape(kind="repeat", left="ab")
    # score() pools retests to drop a slope that noise faked; the quadratic control pools them to recover one that
    # noise hid, so only a runner that hides it five times running counts as blind
    quadratic = measure(_quadratic_control, control)
    for _ in range(_RETESTS - 1):
        if quadratic.super_linear:
            break
        quadratic = quadratic.pooled(measure(_quadratic_control, control))
    if not quadratic.super_linear:
        return f"a quadratic control scored growth {_growth_text(quadratic)}"
    if (linear := score(_linear_control, control)).super_linear:
        return f"a linear control scored growth {_growth_text(linear)}"
    return None


def _quadratic_control(text: str) -> None:
    sum(1 for _ in range(len(text) // 32) for _ in range(len(text) // 32))


def _linear_control(text: str) -> None:
    sum(1 for _ in range(len(text) // 4))


def _growth_text(result: Score) -> str:
    return " ".join(f"{value:.2f}" for value in result.growth)


@dataclass(frozen=True, slots=True, kw_only=True)
class Shape:
    # One input family, rendered at any size.
    #
    # ``repeat`` is ``head + left*N + tail``; ``sandwich`` is ``head + left*N + middle + right*N + tail``; ``tree`` is
    # ``head + middle + left + middle + left*2 + middle ... + tail``. With ``counter`` every ``{n}`` in a repeated piece
    # becomes its repetition index, zero-padded to six digits, so a run of attributes or rules carries distinct names of
    # one length.
    kind: str
    left: str
    middle: str = ""
    right: str = ""
    head: str = ""
    tail: str = ""
    counter: bool = False

    def __post_init__(self) -> None:
        # Reject a shape that cannot grow or whose pieces pass 128 bytes.
        if self.kind not in _KINDS:
            msg = f"shape kind {self.kind!r} is not one of {', '.join(_KINDS)}"
            raise ValueError(msg)
        if not self.left:
            msg = "a shape needs a non-empty left piece to grow"
            raise ValueError(msg)
        if self.kind != "sandwich" and self.right:
            msg = f"a {self.kind} shape has no right piece"
            raise ValueError(msg)
        if self.kind == "repeat" and self.middle:
            msg = "a repeat shape has no middle piece"
            raise ValueError(msg)
        pieces = {"left": self.left, "middle": self.middle, "right": self.right, "head": self.head, "tail": self.tail}
        if long := [name for name, piece in pieces.items() if _width(piece) > _MAX_PIECE]:
            msg = f"{', '.join(long)} exceed {_MAX_PIECE} bytes"
            raise ValueError(msg)

    def render(self, size: int) -> str:
        # Repeat the growing pieces as often as ``size`` UTF-8 bytes allow.
        middle = self.middle if self.kind == "sandwich" else ""
        budget = size - _width(self.head + middle + self.tail)
        lefts: list[str] = []
        rights: list[str] = []
        for index in itertools.count():
            left, right = self._unit(index)
            if (budget := budget - _width(left) - _width(right)) < 0:
                break
            lefts.append(left)
            rights.append(right)
        return self.head + "".join(lefts) + middle + "".join(reversed(rights)) + self.tail

    def _unit(self, index: int) -> tuple[str, str]:
        left, right = (self.left * index + self.middle, "") if self.kind == "tree" else (self.left, self.right)
        if self.counter:
            # a fixed width keeps the cost of comparing two names flat: with bare digits, the pre-#508 scan's growth
            # fell below 2.5 across the 3-to-4-digit step, because names of unequal length skip the memcmp
            number = f"{index:06d}"
            return left.replace(_COUNTER, number), right.replace(_COUNTER, number)
        return left, right


def _width(text: str) -> int:
    return len(text.encode())


@dataclass(frozen=True, slots=True, kw_only=True)
class Score:
    # The best thread CPU seconds per call at each size, and the output size at each; ``too_long`` cut it short.
    sizes: tuple[int, ...]
    seconds: tuple[float, ...]
    characters: tuple[int, ...]
    outputs: tuple[int | None, ...]
    too_long: bool = False

    @property
    def super_linear(self) -> bool:
        # Time growing at least 2.5x at every measured doubling, or a call past the limit at the first size.
        return all(growth >= _MIN_GROWTH for growth in self.growth)

    @property
    def growth(self) -> tuple[float, ...]:
        # Time growth per doubling, normalized to an exact doubling: 2 for linear work, 4 for quadratic.
        steps = zip(self.sizes, self.sizes[1:], self.seconds, self.seconds[1:], strict=False)
        return tuple(2 * (after / before) * (small / large) for small, large, before, after in steps)

    def within(self, bound: Bound | None) -> bool:
        # Whether every measured output fits ``bound``; a target without a bound always fits.
        return bound is None or all(map(bound.holds, self.characters, self.outputs))

    def pooled(self, other: Score) -> Score:
        # Keep the faster sample at each size: noise only ever adds time, so the minimum converges on the cost.
        seconds = tuple(map(min, self.seconds, other.seconds))
        return Score(
            sizes=self.sizes, seconds=seconds, characters=self.characters, outputs=self.outputs, too_long=other.too_long
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Bound:
    # ``len(out) <= slope * len(in) + offset``, both lengths in characters.
    slope: float
    offset: int

    def holds(self, characters: int, output: int | None) -> bool:
        # Whether an output of ``output`` characters (``None`` when the call raised) fits ``characters`` of input.
        return output is None or output <= self.slope * characters + self.offset


@dataclass(frozen=True, slots=True, kw_only=True)
class Target:
    # A public entry point, the C sources its alphabet comes from, and its output bound when it emits text.
    run: Callable[[str], str | None]
    sources: tuple[str, ...]
    bound: Bound | None = None


def score(run: Callable[[str], str | None], shape: Shape, base: int = _BASE_BYTES) -> Score:
    # Measure, and while the verdict is super-linear pool up to four more measurements into it.
    #
    # A scheduler stall inflates a sample at one size and can fake a slope; pooling keeps the fastest sample per size,
    # so a fake slope fades as samples accumulate while a real one stays. A measurement a slow call cut short is final.
    result = measure(run, shape, base)
    for _ in range(_RETESTS - 1):
        if result.too_long or not result.super_linear:
            break
        result = result.pooled(measure(run, shape, base))
    return result


def measure(run: Callable[[str], str | None], shape: Shape, base: int = _BASE_BYTES) -> Score:
    # Render ``shape`` at ``base`` bytes and three doublings, timing ``run`` on each.
    #
    # The samples of all sizes interleave, round by round, so a slow stretch of the runner lands on every size alike
    # instead of on one.
    sizes: list[int] = []
    characters: list[int] = []
    outputs: list[int | None] = []
    texts: list[str] = []
    batches: list[int] = []
    best: list[float] = []
    best_of: int = 3
    doublings = 3
    with _gc_paused():
        for step in range(doublings + 1):
            texts.append(text := shape.render(base << step))
            with _watchdog():
                start = time.thread_time()
                output = _call(run, text)
                sizes.append(_width(text))
                characters.append(len(text))
                outputs.append(None if output is None else len(output))
                if (first := time.thread_time() - start) > _TOO_LONG_SECONDS:
                    seconds = (*best, first)
                    return Score(
                        sizes=tuple(sizes),
                        seconds=seconds,
                        characters=tuple(characters),
                        outputs=tuple(outputs),
                        too_long=True,
                    )
                loops, elapsed = _calibrate(run, text)
            batches.append(loops)
            best.append(elapsed / loops)
        for _ in range(best_of - 1):
            for index, (text, loops) in enumerate(zip(texts, batches, strict=True)):
                with _watchdog():
                    best[index] = min(best[index], _batch(run, text, loops) / loops)
    return Score(sizes=tuple(sizes), seconds=tuple(best), characters=tuple(characters), outputs=tuple(outputs))


@contextmanager
def _gc_paused() -> Generator[None, None, None]:
    # a collection lands on whichever sample crosses the allocation threshold, so timeit runs with the collector off too
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


@contextmanager
def _watchdog() -> Generator[None, None, None]:
    # a hung call never returns to Python, so a C-level watchdog dumps the stack and exits the process instead
    faulthandler.dump_traceback_later(30.0, exit=True, file=_STDERR)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def _call(run: Callable[[str], str | None], text: str) -> str | None:
    try:
        return run(text)
    except _EXPECTED:
        return None


def _calibrate(run: Callable[[str], str | None], text: str) -> tuple[int, float]:
    # Double the calls per sample until one sample passes the minimum; return that count and the sample.
    loops = 1
    while (elapsed := _batch(run, text, loops)) < _MIN_SAMPLE_SECONDS:
        loops *= 2
    return loops, elapsed


def _batch(run: Callable[[str], str | None], text: str, loops: int) -> float:
    start = time.thread_time()
    for _ in range(loops):
        _call(run, text)
    return time.thread_time() - start


@dataclass(frozen=True, slots=True, kw_only=True)
class Regression:
    # A super-linear input fixed on main, re-scored on every pull request.
    name: str
    target: str
    shape: Shape
    source: str
    base: int = _BASE_BYTES
    slope: float | None = None  # replaces the target's output slope for a cap that legitimately amplifies


def load_regressions(path: Path) -> list[Regression]:
    # Read the regression list, one row per target a committed shape runs through.
    rows = []
    for entry in json.loads(path.read_text(encoding="utf-8")):
        if unknown := sorted(set(entry["targets"]) - TARGETS.keys()):
            msg = f"{entry['name']}: unknown target {', '.join(unknown)}"
            raise ValueError(msg)
        shape = Shape(**entry["shape"])
        base = entry.get("base", _BASE_BYTES)
        rows.extend(
            Regression(
                name=entry["name"],
                target=target,
                shape=shape,
                source=entry["source"],
                base=base,
                slope=entry.get("slope"),
            )
            for target in entry["targets"]
        )
    return rows


def _rescore(rows: list[Regression], targets: Sequence[str] | None) -> int:
    selected = [row for row in rows if targets is None or row.target in targets]
    failed = 0
    for row in selected:
        # the name goes out first, so a run the hang watchdog ends still names its row
        print(f"{row.name} [{row.target}] growth ", end="", flush=True)
        target = TARGETS[row.target]
        result = score(target.run, row.shape, row.base)
        verdict = "super-linear" if result.super_linear else "linear"
        if result.too_long:
            verdict += f", cut short by a call over {_TOO_LONG_SECONDS} s"
        bound = target.bound if row.slope is None or target.bound is None else replace(target.bound, slope=row.slope)
        if not (fits := result.within(bound)):
            verdict += ", output over bound"
        print(f"{_growth_text(result)}: {verdict}", flush=True)
        failed += result.super_linear or not fits
    print(f"{len(selected)} regression rows scored, {failed} failed")
    return int(failed > 0)


def _search(targets: Sequence[str], budget: _Budget, rng_seed: int, crash_dir: Path, process: _ProcessFactory) -> int:
    # Run the hunt in worker processes until the shape count or the wall-clock budget is spent.
    #
    # The worker writes each shape to ``inflight.json`` before scoring it, so when the hang watchdog or a crash kills
    # the worker, the shape it died on is still on disk; a fresh worker with its own seed stream takes the rest of the
    # budget. A shape count replaces the budget, and the count shared across workers is what ends the restarts.
    crash_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + budget.seconds
    inflight = crash_dir / _INFLIGHT
    status = 0
    restart = 0
    while True:
        remaining = replace(budget, seconds=deadline - time.monotonic())
        worker = process(target=_hunt, args=(targets, remaining, f"{rng_seed}/{restart}", crash_dir))
        worker.start()
        worker.join()
        log = crash_dir / f"crash-worker-{restart}.log"
        if not inflight.exists():
            log.unlink(missing_ok=True)  # absent when the worker died before it opened its log
            return status | int(worker.exitcode != 0)
        status = 1
        entry = json.loads(inflight.read_text(encoding="utf-8"))
        details = {"rng-seed": entry["rng-seed"], "finding": "abort", "exitcode": worker.exitcode, "log": log.name}
        _record(crash_dir, entry["target"], entry["shape"], details)
        inflight.unlink()
        if time.monotonic() >= deadline:
            return status
        restart += 1


@dataclass(frozen=True, slots=True, kw_only=True)
class _Budget:
    # Wall-clock seconds and a shape count for the whole search, with the shared counter that enforces the count.
    seconds: float
    shapes: int | None
    drawn: c_int


def _hunt(targets: Sequence[str], budget: _Budget, seed: str, crash_dir: Path) -> None:
    # the watchdog's traceback and an unexpected exception may quote the input, so stderr goes to the private dir
    with (crash_dir / f"crash-worker-{seed.rpartition('/')[2]}.log").open("w", encoding="utf-8") as log:
        os.dup2(log.fileno(), _STDERR)
    rng = random.Random(seed)
    literals = {name: alphabet(TARGETS[name].sources) for name in targets}
    inflight = crash_dir / _INFLIGHT
    tried: collections.Counter[str] = collections.Counter()
    found = capped = 0
    deadline = time.monotonic() + budget.seconds
    while time.monotonic() < deadline and (budget.shapes is None or budget.drawn.value < budget.shapes):
        name = rng.choice(targets)
        shape = _draw(rng, literals[name])
        fields = asdict(shape)
        budget.drawn.value += 1  # counted before scoring, so the shape that kills a worker still uses up the cap
        inflight.write_text(json.dumps({"target": name, "shape": fields, "rng-seed": seed}), encoding="utf-8")
        target = TARGETS[name]
        result = score(target.run, shape)
        slope = result.super_linear and score(target.run, shape, _PAST_CAPS).super_linear
        capped += result.super_linear and not slope
        if slope or not result.within(target.bound):
            finding = "super-linear" if slope else "output"
            _record(crash_dir, name, fields, {"rng-seed": seed, "finding": finding} | _measurements(result))
            found += 1
        inflight.unlink()
        tried[name] += 1
    total = sum(tried.values())
    print(f"search: {total} shapes over {len(tried)} targets, {capped} capped slope(s), {found} finding(s)", flush=True)
    sys.exit(int(found > 0))


def _draw(rng: random.Random, literals: Sequence[str]) -> Shape:
    kind = rng.choice(_KINDS)
    left = _phrase(rng, literals)
    if counter := rng.random() < 0.25:
        at = rng.randint(0, len(left))
        left = _fit(left[:at] + _COUNTER + left[at:])
    return Shape(
        kind=kind,
        left=left,
        middle=_phrase(rng, literals) if kind != "repeat" else "",
        right=_phrase(rng, literals) if kind == "sandwich" else "",
        head=_phrase(rng, literals) if rng.random() < 0.5 else "",
        tail=_phrase(rng, literals) if rng.random() < 0.5 else "",
        counter=counter,
    )


def _phrase(rng: random.Random, literals: Sequence[str]) -> str:
    # pulldown-cmark's COMBINATIONS: a pattern concatenates up to six random literals
    return _fit("".join(rng.choices(literals, k=rng.randint(1, 6))))


def _fit(text: str) -> str:
    while _width(text) > _MAX_PIECE:
        text = text[:-1]
    return text


def _measurements(result: Score) -> dict[str, _JSON]:
    return {
        "sizes": result.sizes,
        "seconds": result.seconds,
        "growth": result.growth,
        "characters": result.characters,
        "outputs": result.outputs,
        "too-long": result.too_long,
    }


def _record(crash_dir: Path, target: str, shape: dict[str, _JSON], details: dict[str, _JSON]) -> None:
    identity = {"target": target, "shape": shape}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    (crash_dir / f"crash-{digest}.json").write_text(f"{json.dumps(identity | details, indent=2)}\n", encoding="utf-8")
    print(f"FINDING [{target}] {shape['kind']} sha256={digest}", flush=True)


# pulldown-cmark harvests the literals its parser branches on
# (https://github.com/pulldown-cmark/pulldown-cmark/blob/c61583e33f043e926a5cbd4423252c6cb97301d2/dos-fuzzer/src/literals.rs#L8-L15);
# the C equivalents are case labels, string arguments of comparison helpers, and static keyword tables
_COMMENT_OR_LITERAL: Final[re.Pattern[str]] = re.compile(
    r'(?#double quoted string)"(?:[^"\\\n]|\\.)*"'
    r"|(?#single quoted string)'(?:[^'\\\n]|\\.)*'"
    r"|(?#block comment)/\*.*?\*/"
    r"|(?#line comment)//[^\n]*",
    re.DOTALL,
)
_CHAR: Final[str] = r"'((?:[^'\\\n]|\\[^\n]){1,8})'"
_COMPARED_CHAR: Final[re.Pattern[str]] = re.compile(rf"\bcase\s+{_CHAR}\s*:|[=!]=\s*{_CHAR}|{_CHAR}\s*[=!]=")
_CASE_CODE_POINT: Final[re.Pattern[str]] = re.compile(
    r"(?#case label)\bcase\s+(?#hex prefix)0[xX](?P<digits>[0-9A-Fa-f]+)(?#optional suffix)[uU]?(?#colon)\s*:"
)
_STATEMENT_END: Final[re.Pattern[str]] = re.compile(r"[;{}]")
# a call to a comparison helper (memcmp, match_kw, css_run_ieq, u_eq_ascii, is_schema_el, ...) marks its statement
_COMPARISON: Final[re.Pattern[str]] = re.compile(
    r"(?#helper name prefix or suffix)(?:cmp|eq|match|starts_with|ends_with|_kw|lookup|is_|_is)"
    r"(?#rest of the name)\w*(?#optional closing parenthesis)\)?(?#whitespace)\s*(?#opening parenthesis)\("
)
_TABLE: Final[re.Pattern[str]] = re.compile(r"static\s+const\s[^=;{}]*\[[^\]]*\]\s*=\s*\{(.*?)\};", re.DOTALL)
_STRING: Final[re.Pattern[str]] = re.compile(
    r'(?#opening quote)"(?P<body>(?#characters that are plain or escaped)(?:[^"\\\n]|\\.)*)(?#closing quote)"'
)
_ESCAPE: Final[re.Pattern[bytes]] = re.compile(
    rb"(?#backslash)\\(?P<code>(?#hex escape)x[0-9A-Fa-f]{1,2}|(?#octal escape)[0-7]{1,3}|(?#any other character).)"
)
# the tag atom table holds bare element names; the tree builder branches on them as start and end tags
_TAG_TABLE: Final[str] = "data/tag_atom.h"


def alphabet(sources: tuple[str, ...]) -> tuple[str, ...]:
    # Harvest the literals the C code under ``sources`` (globs relative to ``src/turbohtml/_c``) branches on.
    literals = set().union(*(_harvest(path) for pattern in sources for path in _C_SOURCES.glob(pattern)))
    # like pulldown-cmark, drop multi-character literals with a newline: those are messages, not syntax
    max_literal = 32
    return tuple(
        sorted(
            literal
            for literal in literals
            if literal and len(literal) <= max_literal and ("\n" not in literal or len(literal) == 1)
        )
    )


@cache
def _harvest(path: Path) -> frozenset[str]:
    code = _COMMENT_OR_LITERAL.sub(_drop_comment, path.read_text(encoding="utf-8"))
    ends = [match.start() for match in _STATEMENT_END.finditer(code)]
    statements = {bisect.bisect(ends, match.start()) for match in _COMPARISON.finditer(code)}
    regions = [
        code[ends[index - 1] if index else 0 : ends[index] if index < len(ends) else None] for index in statements
    ]
    strings = {_unescape(literal) for region in regions + _TABLE.findall(code) for literal in _STRING.findall(region)}
    if path.relative_to(_C_SOURCES).as_posix() == _TAG_TABLE:
        strings |= {f"{bracket}{name}>" for name in strings for bracket in ("<", "</")}
    chars = {_unescape(char) for groups in _COMPARED_CHAR.findall(code) for char in groups if char}
    code_points = {chr(value) for match in _CASE_CODE_POINT.findall(code) if _is_scalar(value := int(match, 16))}
    return frozenset(strings | chars | code_points)


def _drop_comment(match: re.Match[str]) -> str:
    return match[0] if match[0][0] in "\"'" else " "


def _unescape(literal: str) -> str:
    simple_escapes = {b"n": b"\n", b"t": b"\t", b"r": b"\r", b"f": b"\f", b"v": b"\v", b"a": b"\a", b"b": b"\b"}

    def replace(match: re.Match[bytes]) -> bytes:
        if (code := match["code"]).startswith(b"x"):
            return bytes([int(code[1:], 16)])
        if code[:1].isdigit() and code[:1] < b"8":
            return bytes([int(code, 8) & 0xFF])
        return simple_escapes.get(code, code)

    return _ESCAPE.sub(replace, literal.encode()).decode(errors="replace")


def _is_scalar(code: int) -> bool:
    return code < 0x110000 and not 0xD800 <= code <= 0xDFFF


def _drain(text: str) -> None:
    collections.deque(tokenize(text), maxlen=0)


def _parse(text: str) -> None:
    parse(text)


def _serialize(text: str) -> str:
    return parse(text).serialize()


def _to_markdown(text: str) -> str:
    return parse(text).to_markdown()


def _to_text(text: str) -> str:
    return parse(text).to_text()


def _select_input(text: str) -> None:
    _SELECTOR_HOST.select(text)


def _select_tree(text: str) -> None:
    document = parse(text)
    for selector in _TREE_SELECTORS:
        document.select(selector)


def _xpath_input(text: str) -> None:
    _XPATH_HOST.xpath(text)


def _xpath_tree(text: str) -> str:
    document = parse_xml(text)
    return "".join(str(document.xpath(expression)) for expression in _TREE_XPATHS)


def _url(text: str) -> None:
    normalize_url(text)
    clean_url(text)


def _idna(text: str) -> None:
    normalize_url(f"http://{text}/")


def _parse_xml(text: str) -> None:
    parse_xml(text)


def _relaxng(text: str) -> None:
    RelaxNG(text)


def _relaxng_validate(text: str) -> None:
    _AMBIGUOUS_GRAMMAR.validate(parse_xml(text))


def _xsd(text: str) -> None:
    XMLSchema(text)


def _xslt(text: str) -> str:
    return _STYLESHEET(parse_xml(text))


_SELECTOR_HOST: Final[Document] = parse(
    "<div id=a class='x y' data-v=ab><p lang=en-US>t<a href=/u>l</a></p><ul><li>1<li>2</ul><input type=text></div>"
)
# a linear-time spread of the engine: descendant and sibling combinators, :has (memoized since #509), nth, attributes
_TREE_SELECTORS: Final[tuple[str, ...]] = (
    "div p",
    "a ~ b",
    "div:has(a)",
    "li:nth-child(2n+1)",
    "[data-v*='ab']",
    ":not(:empty)",
)
_XPATH_HOST: Final[Document] = parse_xml("<r a='1'><s b='x'>text</s><s b='y'><t/></s><!--c--><?p d?></r>")
# node walks plus the EXSLT functions that size their result from document data (str:padding is #957)
_TREE_XPATHS: Final[tuple[str, ...]] = (
    "count(//*)",
    "count(//@*)",
    "string(/)",
    "str:padding(string(//@*[1]), '-')",
    "count(set:distinct(//@*))",
)
_XML_SOURCES: Final[tuple[str, ...]] = ("tokenizer/xml.c", "tokenizer/xml_names.h", "dom/*")
_HTML_SOURCES: Final[tuple[str, ...]] = ("tokenizer/*", "dom/*", _TAG_TABLE, "data/attr_atom.h")
_AMBIGUOUS_GRAMMAR: Final[RelaxNG] = RelaxNG(
    '<element name="doc" xmlns="http://relaxng.org/ns/structure/1.0"><oneOrMore><choice>'
    '<element name="a"><empty/></element>'
    '<group><element name="a"><empty/></element><element name="a"><empty/></element></group>'
    "</choice></oneOrMore></element>"
)
# an identity copy plus every element name sorted against document order, the worst case of the insertion sort #652
# replaced
_STYLESHEET: Final[Transform] = Transform(
    parse_xml(
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:template match="/"><out><xsl:apply-templates/>'
        '<xsl:for-each select="//*"><xsl:sort select="name()" order="descending"/>'
        '<n><xsl:value-of select="name()"/></n></xsl:for-each></out>'
        "</xsl:template>"
        '<xsl:template match="@*|node()"><xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy></xsl:template>'
        "</xsl:stylesheet>"
    )
)
# Output bounds, in characters. The measurements behind them: the html5lib tree-construction inputs, the readability
# pages and tools/fuzz/corpus for the HTML targets (plus the linkify-it fixtures for linkify), normalize.css and pico
# for CSS, underscore, backbone and tests/js/_corpus for JS, the libxslt test documents for XSLT and XPath, and every
# harvested literal repeated to 8 KiB. The offsets cover the largest output of an input of at most 64 characters.
_SERIALIZED: Final[Bound] = Bound(
    slope=6.0, offset=256
)  # &quot; and &nbsp; turn one character into six; corpora peak 2.20, offset 230
TARGETS: Final[dict[str, Target]] = {
    "tokenize": Target(run=_drain, sources=_HTML_SOURCES),
    "parse": Target(run=_parse, sources=_HTML_SOURCES),
    "serialize": Target(run=_serialize, sources=(*_HTML_SOURCES, "serialize/*"), bound=_SERIALIZED),
    # a run of <h6> peaks at 2.25 and a table of empty cells at 3.00; corpora peak 1.00, offset 46
    "to_markdown": Target(
        run=_to_markdown, sources=(*_HTML_SOURCES, "serialize/markdown*"), bound=Bound(slope=4.0, offset=256)
    ),
    # nested blockquotes indent lines by up to TH_MAX_INDENT_LEVELS steps of 4 spaces, peaking at 3.42;
    # corpora and repeated literals both peak at 1.00, offset 45
    "to_text": Target(run=_to_text, sources=(*_HTML_SOURCES, "serialize/text.c"), bound=Bound(slope=4.0, offset=64)),
    "sanitize": Target(run=sanitize, sources=(*_HTML_SOURCES, "clean/sanitize.c"), bound=_SERIALIZED),
    # "a.co " becomes a 46-character nofollow anchor, 9.2 times its length; corpora peak 2.26, offset 191
    "linkify": Target(
        run=linkify, sources=(*_HTML_SOURCES, "clean/linkify.c", "url/*"), bound=Bound(slope=10.0, offset=256)
    ),
    # corpora and repeated literals both peak at 1.00
    "minify_css": Target(run=minify_css, sources=("css/minify/*",), bound=Bound(slope=2.0, offset=64)),
    # a run of "#" peaks at 2.00; corpora peak 0.98, offset 61
    "minify_js": Target(run=minify_js, sources=("js/*",), bound=Bound(slope=3.0, offset=64)),
    "selector": Target(run=_select_input, sources=("css/select/*",)),
    "select_tree": Target(run=_select_tree, sources=_HTML_SOURCES),
    "xpath": Target(run=_xpath_input, sources=("query/xpath/*",)),
    # string(/) never outgrows the document; str:padding stops at 100,000 characters (#957), the counts are short
    "xpath_tree": Target(
        run=_xpath_tree, sources=(*_XML_SOURCES, "query/xpath/*"), bound=Bound(slope=1.0, offset=100_064)
    ),
    "url": Target(run=_url, sources=("url/*",)),
    "idna": Target(run=_idna, sources=("url/idna.c", "url/url.c")),
    "parse_xml": Target(run=_parse_xml, sources=_XML_SOURCES),
    "relaxng": Target(run=_relaxng, sources=(*_XML_SOURCES, "validate/relaxng.h", "validate/schema.c")),
    "relaxng_validate": Target(run=_relaxng_validate, sources=_XML_SOURCES),
    "xsd": Target(run=_xsd, sources=(*_XML_SOURCES, "validate/xsd.h", "validate/datatypes.h", "validate/schema.c")),
    # the copy plus one <n>name</n> per element: a run of <a/> peaks at 3.00; corpora peak 1.71, offset 134
    "xslt": Target(run=_xslt, sources=_XML_SOURCES, bound=Bound(slope=4.0, offset=256)),
}


__all__ = [
    "TARGETS",
    "Bound",
    "Regression",
    "Score",
    "Shape",
    "Target",
    "alphabet",
    "load_regressions",
    "main",
    "measure",
    "score",
]


if __name__ == "__main__":
    raise SystemExit(main())
