r"""
Compile libphonenumber's bounded regular expressions into automata the C recognizer can walk without a regex engine.

The metadata patterns use only literals, bracket classes, ``\\d``, alternation, non-capturing groups, ``?``, ``{n}``,
``{n,m}``, capturing groups and a trailing ``$``; nothing else is accepted, so every language here is finite. One
Thompson-style program (``CHAR``/``SPLIT``/``SAVE``/``END``/``MATCH``, RE2's ``Prog`` shape) is built from Python's own
parse tree and then read three ways: an unordered subset construction with labeled accepts (the numbering plans, the
routers, the extension grammar), an ordered-thread construction that reproduces Java's ``lookingAt`` choice without
captures (the national-prefix and IDD patterns), and a Pike VM that recovers the transform group once that choice has
fixed the match end. The digit alphabet is the symbols 0-9 plus ``END``; other alphabets pass a symbolizer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise, starmap
from typing import TYPE_CHECKING, Final, TypeAlias, cast

try:
    from re import _constants as sre_constants  # ty: ignore[unresolved-import]  # sre_parse's private home since 3.11
    from re import _parser as sre_parse  # ty: ignore[unresolved-import]  # sre_parse's private home since 3.11
except ImportError:  # Python 3.10 keeps the pre-3.11 module names
    import sre_constants  # ty: ignore[unresolved-import]  # removed from the stubs after its deprecation
    import sre_parse  # ty: ignore[unresolved-import]  # removed from the stubs after its deprecation

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

DIGIT_SYMBOLS: Final = 10
_END: Final = 10
ALL_DIGITS: Final = (1 << DIGIT_SYMBOLS) - 1

CHAR: Final = 0
SPLIT: Final = 1
SAVE: Final = 2
ASSERT_END: Final = 3
MATCH: Final = 4

_Symbolizer: TypeAlias = "Callable[[int], int]"
# sre_parse's node list: (opcode, argument) pairs whose argument shape the stdlib leaves untyped, so each site casts
SreItems: TypeAlias = list[tuple[object, object]]
_PriorityKey: TypeAlias = tuple[tuple[int, ...], int]


class UnsupportedPatternError(ValueError):
    """The pattern uses a construct outside the metadata subset (an unbounded repeat, a negated class, lookaround)."""


@dataclass
class _Op:
    """One program instruction; ``next`` and ``alt`` index the program, ``arg`` is a symbol mask, a slot or a label."""

    kind: int
    arg: int = 0
    next: int = -1
    alt: int = -1


@dataclass
class Program:
    """An ordered NFA program; ``split`` prefers ``next`` over ``alt``, which encodes Java's evaluation order."""

    ops: list[_Op]
    start: int
    slots: int


@dataclass
class Dfa:
    """Row 0 is the dead state, row 1 the start state; ``accepts[state]`` is 0 when the state does not accept."""

    symbols: int
    next: list[list[int]]
    accepts: list[int]


@dataclass
class PriorityDfa:
    """
    The ordered-thread automaton for a prefix or IDD pattern.

    Per state: ``accept`` when a provisional accept exists, ``final`` when no higher-priority thread is alive (the
    moment Java's ``lookingAt`` returns), ``offset_back`` the digits consumed since the accepted end. A final state has
    no outgoing edges.
    """

    next: list[list[int]]
    accept: list[bool]
    final: list[bool]
    offset_back: list[int]


def _digit_symbolizer(code: int) -> int:
    """Map an ASCII digit code point to its symbol mask; anything else is outside the digit alphabet."""
    if 0x30 <= code <= 0x39:
        return 1 << (code - 0x30)
    msg = f"code point U+{code:04X} is not a digit"
    raise UnsupportedPatternError(msg)


@dataclass
class _Fragment:
    start: int
    outs: list[tuple[int, str]] = field(default_factory=list)


class _Builder:
    def __init__(self, symbolize: _Symbolizer, digit_mask: int, *, capture: bool, allow_unbounded: bool) -> None:
        self.ops: list[_Op] = []
        self.symbolize = symbolize
        self.digit_mask = digit_mask
        self.capture = capture
        self.allow_unbounded = allow_unbounded
        self.slots = 0

    def emit(self, kind: int, arg: int = 0) -> int:
        self.ops.append(_Op(kind, arg))
        return len(self.ops) - 1

    def patch(self, outs: Iterable[tuple[int, str]], target: int) -> None:
        for index, attribute in outs:
            setattr(self.ops[index], attribute, target)

    def sequence(self, items: Sequence[tuple[object, object]]) -> _Fragment:
        fragments = list(starmap(self.item, items))
        if not fragments:
            return self._empty()
        for previous, following in pairwise(fragments):
            self.patch(previous.outs, following.start)
        return _Fragment(fragments[0].start, fragments[-1].outs)

    def _empty(self) -> _Fragment:
        # an empty sequence is a pass-through: a SAVE with no slot effect would cost a real slot, so use a SPLIT whose
        # two arms both wait for the same patch target
        index = self.emit(SPLIT)
        return _Fragment(index, [(index, "next"), (index, "alt")])

    def item(  # ruff:ignore[too-many-return-statements, complex-structure]  # one arm per sre opcode
        self, opcode: object, argument: object
    ) -> _Fragment:
        if opcode is sre_constants.LITERAL:
            index = self.emit(CHAR, self.symbolize(cast("int", argument)))
            return _Fragment(index, [(index, "next")])
        if opcode is sre_constants.IN:
            index = self.emit(CHAR, self.class_mask(cast("SreItems", argument)))
            return _Fragment(index, [(index, "next")])
        if opcode is sre_constants.AT:
            if argument is not sre_constants.AT_END:
                msg = f"unsupported anchor {argument}"
                raise UnsupportedPatternError(msg)
            index = self.emit(ASSERT_END)
            return _Fragment(index, [(index, "next")])
        if opcode is sre_constants.SUBPATTERN:
            group, _add, _remove, items = cast("tuple[int | None, int, int, SreItems]", argument)
            if group is None or not self.capture:
                return self.sequence(items)
            open_index = self.emit(SAVE, 2 * group)
            inner = self.sequence(items)
            close_index = self.emit(SAVE, 2 * group + 1)
            self.ops[open_index].next = inner.start
            self.patch(inner.outs, close_index)
            self.slots = max(self.slots, 2 * group + 2)
            return _Fragment(open_index, [(close_index, "next")])
        if opcode is sre_constants.BRANCH:
            _unused, alternatives = cast("tuple[None, list[SreItems]]", argument)
            return self.branch([self.sequence(alternative) for alternative in alternatives])
        if opcode is sre_constants.MAX_REPEAT:
            low, high, items = cast("tuple[int, object, SreItems]", argument)
            if high is sre_constants.MAXREPEAT:
                if not self.allow_unbounded:
                    msg = "unbounded repeat"
                    raise UnsupportedPatternError(msg)
                return self.star(low, items)
            return self.repeat(low, cast("int", high), items)
        msg = f"unsupported construct {opcode}"
        raise UnsupportedPatternError(msg)

    def class_mask(self, items: SreItems) -> int:
        mask = 0
        for opcode, argument in items:
            if opcode is sre_constants.LITERAL:
                mask |= self.symbolize(cast("int", argument))
            elif opcode is sre_constants.RANGE:
                low, high = cast("tuple[int, int]", argument)
                for code in range(low, high + 1):
                    mask |= self.symbolize(code)
            elif opcode is sre_constants.CATEGORY and argument is sre_constants.CATEGORY_DIGIT:
                mask |= self.digit_mask
            else:
                msg = f"unsupported class item {opcode}"
                raise UnsupportedPatternError(msg)
        return mask

    def branch(self, alternatives: list[_Fragment]) -> _Fragment:
        # nested splits in source order: the first alternative has the highest priority
        fragment = alternatives[-1]
        for alternative in reversed(alternatives[:-1]):
            index = self.emit(SPLIT)
            self.ops[index].next = alternative.start
            self.ops[index].alt = fragment.start
            fragment = _Fragment(index, alternative.outs + fragment.outs)
        return fragment

    def repeat(self, low: int, high: int, items: Sequence[tuple[object, object]]) -> _Fragment:
        copies = [self.sequence(items) for _ in range(high)]
        if not copies:
            return self._empty()
        for previous, following in pairwise(copies):
            self.patch(previous.outs, following.start)
        # a greedy optional copy prefers entering the copy over skipping it, which is Java's order
        tail_outs: list[tuple[int, str]] = []
        optional_start = -1
        for copy in reversed(copies[low:]):
            index = self.emit(SPLIT)
            self.ops[index].next = copy.start
            if optional_start >= 0:
                self.patch(copy.outs, optional_start)
            else:
                tail_outs += copy.outs
            tail_outs.append((index, "alt"))
            optional_start = index
        if low == 0:
            return _Fragment(optional_start, tail_outs)
        if optional_start >= 0:
            self.patch(copies[low - 1].outs, optional_start)
            return _Fragment(copies[0].start, tail_outs)
        return _Fragment(copies[0].start, copies[low - 1].outs)

    def star(self, low: int, items: Sequence[tuple[object, object]]) -> _Fragment:
        # ``x{low,}``: the mandatory copies, then a greedy loop that prefers another copy over leaving
        copies = [self.sequence(items) for _ in range(low)]
        loop_split = self.emit(SPLIT)
        body = self.sequence(items)
        self.ops[loop_split].next = body.start
        self.patch(body.outs, loop_split)
        for previous, following in pairwise(copies):
            self.patch(previous.outs, following.start)
        if copies:
            self.patch(copies[-1].outs, loop_split)
            return _Fragment(copies[0].start, [(loop_split, "alt")])
        return _Fragment(loop_split, [(loop_split, "alt")])


def compile_program(  # ruff:ignore[too-many-arguments]  # a public API test module calls these as plain keywords; a config object would break it
    pattern: str,
    *,
    symbolize: _Symbolizer = _digit_symbolizer,
    digit_mask: int = ALL_DIGITS,
    label: int = 1,
    capture: bool = True,
    allow_unbounded: bool = False,
) -> Program:
    """Compile ``pattern`` into an ordered program whose single ``MATCH`` carries ``label``."""
    builder = _Builder(symbolize, digit_mask, capture=capture, allow_unbounded=allow_unbounded)
    fragment = builder.sequence(list(sre_parse.parse(pattern)))
    builder.patch(fragment.outs, builder.emit(MATCH, label))
    return Program(builder.ops, fragment.start, builder.slots)


def union_program(programs: Sequence[Program]) -> Program:
    """Join programs under one start so a subset construction labels each accept with its own program's label."""
    ops: list[_Op] = []
    starts: list[int] = []
    slots = 0
    for program in programs:
        offset = len(ops)
        ops.extend(
            _Op(op.kind, op.arg, op.next + offset if op.next >= 0 else -1, op.alt + offset if op.alt >= 0 else -1)
            for op in program.ops
        )
        starts.append(program.start + offset)
        slots = max(slots, program.slots)
    start = starts[-1]
    for alternative in reversed(starts[:-1]):
        ops.append(_Op(SPLIT, 0, alternative, start))
        start = len(ops) - 1
    return Program(ops, start, slots)


def sticky_program(program: Program, all_symbols: int) -> Program:
    """Make each accept persist over any further symbols, so a router reports every prefix that matched so far."""
    ops = [_Op(op.kind, op.arg, op.next, op.alt) for op in program.ops]
    for index in [position for position, op in enumerate(ops) if op.kind == MATCH]:
        loop_split = len(ops)
        ops.extend((
            _Op(SPLIT, 0, loop_split + 1, loop_split + 2),
            _Op(CHAR, all_symbols, loop_split),
            _Op(MATCH, ops[index].arg),
        ))
        ops[index] = _Op(SPLIT, 0, loop_split + 1, loop_split + 2)
    return Program(ops, program.start, program.slots)


def _closure(program: Program, seeds: Iterable[int]) -> list[int]:
    """List the consuming and matching instructions reachable through epsilons, in priority order, first visit wins."""
    ordered: list[int] = []
    seen: set[int] = set()
    stack: list[int] = list(reversed(list(seeds)))
    while stack:
        index = stack.pop()
        if index in seen:
            continue
        seen.add(index)
        op = program.ops[index]
        if op.kind == SPLIT:
            stack.extend((op.alt, op.next))
        elif op.kind == SAVE:
            stack.append(op.next)
        else:
            ordered.append(index)
    return ordered


def compile_dfa(program: Program, symbols: int = DIGIT_SYMBOLS + 1, end_symbol: int = _END) -> Dfa:
    """Subset construction with labeled accepts (the OR of the reached ``MATCH`` labels), then Moore minimization."""
    start_set = frozenset(_closure(program, [program.start]))
    index_of: dict[frozenset[int], int] = {frozenset(): 0, start_set: 1}
    rows: dict[int, list[int]] = {0: [0] * symbols}
    accepts: dict[int, int] = {0: 0}
    pending = [start_set]
    while pending:
        current = pending.pop()
        row = [0] * symbols
        for symbol in range(symbols):
            seeds = [program.ops[index].next for index in current if _consumes(program.ops[index], symbol, end_symbol)]
            target = frozenset(_closure(program, seeds)) if seeds else frozenset()
            if target not in index_of:
                index_of[target] = len(index_of)
                pending.append(target)
            row[symbol] = index_of[target]
        state = index_of[current]
        rows[state] = row
        label = 0
        for index in current:
            if program.ops[index].kind == MATCH:
                label |= program.ops[index].arg
        accepts[state] = label
    count = len(index_of)
    return minimize(Dfa(symbols, [rows[state] for state in range(count)], [accepts[state] for state in range(count)]))


def _consumes(op: _Op, symbol: int, end_symbol: int = _END) -> bool:
    if op.kind == CHAR:
        return symbol != end_symbol and bool(op.arg >> symbol & 1)
    return op.kind == ASSERT_END and symbol == end_symbol


def minimize(dfa: Dfa) -> Dfa:
    """Moore partition refinement on the accept labels, keeping the dead state at 0 and the start state at 1."""
    count = len(dfa.next)
    block = [dfa.accepts[state] + (1 if state == 0 else 2) for state in range(count)]
    block[0] = 0
    while True:
        signatures: dict[tuple[int, ...], int] = {}
        new_block = [0] * count
        for state in range(count):
            signature = (block[state], *[block[target] for target in dfa.next[state]])
            if signature not in signatures:
                signatures[signature] = len(signatures)
            new_block[state] = signatures[signature]
        if new_block == block:
            break
        block = new_block
    dead_block = block[0]
    start_block = block[1]
    order = [dead_block, start_block, *sorted({value for value in block if value not in {dead_block, start_block}})]
    renumber = {value: position for position, value in enumerate(order)}
    representative: dict[int, int] = {}
    for state in range(count):
        representative.setdefault(block[state], state)
    return Dfa(
        dfa.symbols,
        [[renumber[block[target]] for target in dfa.next[representative[value]]] for value in order],
        [dfa.accepts[representative[value]] for value in order],
    )


def compile_priority_dfa(program: Program, symbols: int = DIGIT_SYMBOLS + 1) -> PriorityDfa:
    """
    Build the ordered-thread automaton.

    A state is the priority-ordered live threads ahead of the best provisional accept plus that accept's age, so the
    automaton reports the end Java's backtracker would return.
    """
    start_key = _classify(program, _closure(program, [program.start]), -1)
    index_of: dict[_PriorityKey, int] = {((), -1): 0, start_key: 1}
    rows: dict[int, list[int]] = {0: [0] * symbols}
    pending = [start_key]
    while pending:
        key = pending.pop()
        rows[index_of[key]] = _priority_row(program, key, symbols, index_of, pending)
    keys = sorted(index_of, key=lambda key: index_of[key])
    return _minimize_priority(
        PriorityDfa(
            [rows[state] for state in range(len(keys))],
            [age >= 0 for _threads, age in keys],
            [_is_final(key) for key in keys],
            [max(age, 0) for _threads, age in keys],
        )
    )


def _classify(program: Program, successors: list[int], age: int) -> _PriorityKey:
    for position, index in enumerate(successors):
        if program.ops[index].kind == MATCH:
            # a higher-priority thread accepted here: it replaces any older provisional accept, and everything
            # behind it can never win, so only the threads ahead of it stay alive
            return tuple(successors[:position]), 0
    if not successors and age < 0:
        return (), -1
    return tuple(successors), age


def _priority_row(
    program: Program,
    key: _PriorityKey,
    symbols: int,
    index_of: dict[_PriorityKey, int],
    pending: list[_PriorityKey],
) -> list[int]:
    row = [0] * symbols
    if _is_final(key):
        return row
    threads, age = key
    for symbol in range(symbols):
        seeds = [program.ops[index].next for index in threads if _consumes(program.ops[index], symbol)]
        # the end marker consumes no digit, so the provisional accept does not age across it
        target = _classify(
            program, _closure(program, seeds) if seeds else [], age if symbol == _END or age < 0 else age + 1
        )
        if target not in index_of:
            index_of[target] = len(index_of)
            pending.append(target)
        row[symbol] = index_of[target]
    return row


def _is_final(key: _PriorityKey) -> bool:
    threads, age = key
    return age >= 0 and not threads


def _minimize_priority(dfa: PriorityDfa) -> PriorityDfa:
    count = len(dfa.next)
    labels = [(dfa.accept[state], dfa.final[state], dfa.offset_back[state]) for state in range(count)]
    label_ids: dict[tuple[bool, bool, int], int] = {}
    block = [0] * count
    for state in range(count):
        block[state] = label_ids.setdefault(labels[state], len(label_ids) + 2)
    block[0] = 0
    while True:
        signatures: dict[tuple[int, ...], int] = {}
        new_block = [0] * count
        for state in range(count):
            signature = (block[state], *[block[target] for target in dfa.next[state]])
            new_block[state] = signatures.setdefault(signature, len(signatures))
        if new_block == block:
            break
        block = new_block
    order = [block[0], block[1], *sorted({value for value in block if value not in {block[0], block[1]}})]
    renumber = {value: position for position, value in enumerate(order)}
    representative: dict[int, int] = {}
    for state in range(count):
        representative.setdefault(block[state], state)
    return PriorityDfa(
        [[renumber[block[target]] for target in dfa.next[representative[value]]] for value in order],
        [dfa.accept[representative[value]] for value in order],
        [dfa.final[representative[value]] for value in order],
        [dfa.offset_back[representative[value]] for value in order],
    )


def accepted_lengths(dfa: Dfa, limit: int = 20) -> dict[int, int]:
    """Map each accepted length up to ``limit`` to the OR of the labels accepting at that length."""
    frontier = {1}
    lengths: dict[int, int] = {}
    for length in range(limit + 1):
        label = 0
        for state in frontier:
            label |= dfa.accepts[state]
        if label:
            lengths[length] = label
        frontier = {dfa.next[state][symbol] for state in frontier for symbol in range(DIGIT_SYMBOLS)} - {0}
        if not frontier:
            break
    return lengths


def longest_accept(dfa: PriorityDfa, limit: int = 40) -> int:
    """Measure the longest string the pattern accepts on the priority automaton's accept states."""
    frontier = {1}
    longest = -1
    for length in range(limit + 1):
        if any(dfa.accept[state] for state in frontier):
            longest = length
        frontier = {dfa.next[state][symbol] for state in frontier for symbol in range(DIGIT_SYMBOLS)} - {0}
        if not frontier:
            break
    return longest


def shortest_accept(dfa: PriorityDfa, limit: int = 40) -> int:
    """Measure the shortest string the pattern accepts, or -1 when the language is empty."""
    frontier = {1}
    for length in range(limit + 1):
        if any(dfa.accept[state] for state in frontier):
            return length
        frontier = {dfa.next[state][symbol] for state in frontier for symbol in range(DIGIT_SYMBOLS)} - {0}
        if not frontier:
            break
    return -1


def lag(dfa: PriorityDfa) -> int:
    """Measure the longest wait between a provisional accept and its finalization, the digits a walk may replay."""
    return max(dfa.offset_back)


def match_end(dfa: PriorityDfa, digits: Sequence[int]) -> int:
    """Java's ``lookingAt`` end for ``digits`` followed by the end of input, or -1 when nothing matches."""
    state = 1
    for position, symbol in enumerate(digits):
        if dfa.final[state]:
            return position - dfa.offset_back[state]
        state = dfa.next[state][symbol]
        if state == 0:
            return -1
    if dfa.final[state]:
        return len(digits) - dfa.offset_back[state]
    state = dfa.next[state][_END]
    if state == 0 or not dfa.accept[state]:
        return -1
    return len(digits) - dfa.offset_back[state]


def pike_spans(program: Program, digits: Sequence[int]) -> dict[int, tuple[int, int]] | None:
    """Group spans ``{group: (start, end)}`` of Java's path over exactly ``digits``, or None."""
    slot_positions = _pike_positions(program, digits)
    if slot_positions is None:
        return None
    spans: dict[int, tuple[int, int]] = {}
    for group in range(1, len(slot_positions) // 2):
        start, end = slot_positions[2 * group], slot_positions[2 * group + 1]
        if start >= 0 and end >= 0:
            spans[group] = (start, end)
    return spans


def _pike_positions(program: Program, digits: Sequence[int]) -> list[int] | None:
    threads = _pike_closure_at(program, [(program.start, [-1] * max(program.slots, 2))], 0)
    for position, symbol in enumerate(digits):
        threads = _pike_closure_at(
            program,
            [(op.next, slots) for index, slots in threads if _consumes(op := program.ops[index], symbol)],
            position + 1,
        )
        if not threads:
            return None
    # at the end a MATCH thread stays as it is and an ASSERT_END thread advances; both are judged in priority order
    final_seeds: list[tuple[int, list[int]]] = []
    for index, slots in threads:
        op = program.ops[index]
        if op.kind == MATCH:
            final_seeds.append((index, slots))
        elif op.kind == ASSERT_END:
            final_seeds.append((op.next, slots))
    for index, slots in _pike_closure_at(program, final_seeds, len(digits)):
        if program.ops[index].kind == MATCH:
            return slots
    return None


def _pike_closure_at(
    program: Program, seeds: list[tuple[int, list[int]]], position: int
) -> list[tuple[int, list[int]]]:
    ordered: list[tuple[int, list[int]]] = []
    seen: set[int] = set()
    stack = list(reversed(seeds))
    while stack:
        index, slots = stack.pop()
        if index in seen:
            continue
        seen.add(index)
        op = program.ops[index]
        if op.kind == SPLIT:
            stack.extend(((op.alt, slots), (op.next, slots)))
        elif op.kind == SAVE:
            updated = list(slots)
            updated[op.arg] = position
            stack.append((op.next, updated))
        else:
            ordered.append((index, slots))
    return ordered


def max_threads(program: Program, limit: int = 20) -> int:
    """Measure the largest simultaneous thread count a Pike run reaches over inputs up to ``limit`` digits."""
    frontier = {tuple(_closure(program, [program.start]))}
    high = max(len(threads) for threads in frontier)
    for _ in range(limit + 1):
        successors: set[tuple[int, ...]] = set()
        for threads in frontier:
            for symbol in range(DIGIT_SYMBOLS + 1):
                seeds = [program.ops[index].next for index in threads if _consumes(program.ops[index], symbol)]
                if seeds:
                    successors.add(tuple(_closure(program, seeds)))
        frontier = successors
        if not frontier:
            break
        high = max(high, *(len(threads) for threads in frontier))
    return high


__all__ = [
    "ALL_DIGITS",
    "ASSERT_END",
    "CHAR",
    "DIGIT_SYMBOLS",
    "MATCH",
    "SAVE",
    "SPLIT",
    "Dfa",
    "PriorityDfa",
    "Program",
    "SreItems",
    "UnsupportedPatternError",
    "accepted_lengths",
    "compile_dfa",
    "compile_priority_dfa",
    "compile_program",
    "lag",
    "longest_accept",
    "match_end",
    "max_threads",
    "minimize",
    "pike_spans",
    "shortest_accept",
    "sre_constants",
    "sre_parse",
    "sticky_program",
    "union_program",
]
