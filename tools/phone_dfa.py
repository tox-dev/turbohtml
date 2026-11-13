"""Compile phone metadata patterns into automata for the C recognizer."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, auto
from itertools import pairwise, starmap
from typing import TYPE_CHECKING, Final, TypeAlias, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

DIGIT_SYMBOLS: Final[int] = 10
_END: Final[int] = 10
ALL_DIGITS: Final[int] = (1 << DIGIT_SYMBOLS) - 1

CHAR: Final[int] = 0
SPLIT: Final[int] = 1
SAVE: Final[int] = 2
ASSERT_END: Final[int] = 3
MATCH: Final[int] = 4

_Symbolizer: TypeAlias = "Callable[[int], int]"


class RegexOp(Enum):
    LITERAL = auto()
    IN = auto()
    AT = auto()
    SUBPATTERN = auto()
    BRANCH = auto()
    MAX_REPEAT = auto()
    RANGE = auto()
    CATEGORY = auto()
    CATEGORY_DIGIT = auto()
    AT_END = auto()


RegexNode: TypeAlias = "tuple[RegexOp, RegexArgument]"
RegexItems: TypeAlias = list[RegexNode]
RegexArgument: TypeAlias = (
    int
    | RegexOp
    | RegexItems
    | tuple[int | None, int, int, RegexItems]
    | tuple[None, list[RegexItems]]
    | tuple[int, int | None, RegexItems]
    | tuple[int, int]
)
_PriorityKey: TypeAlias = tuple[tuple[int, ...], int]


def _digit_symbolizer(code: int) -> int:
    # Map an ASCII digit code point to its symbol mask; anything else is outside the digit alphabet.
    if 0x30 <= code <= 0x39:
        return 1 << (code - 0x30)
    msg = f"code point U+{code:04X} is not a digit"
    raise ValueError(msg)


def compile_program(  # ruff:ignore[too-many-arguments]  # a public API test module calls these as plain keywords; a config object would break it
    pattern: str,
    *,
    symbolize: _Symbolizer = _digit_symbolizer,
    digit_mask: int = ALL_DIGITS,
    label: int = 1,
    capture: bool = True,
    allow_unbounded: bool = False,
) -> Program:
    # Compile ``pattern`` into an ordered program whose single ``MATCH`` carries ``label``.
    builder = _Builder(symbolize, digit_mask, capture=capture, allow_unbounded=allow_unbounded)
    fragment = builder.sequence(list(parse_regex(pattern)))
    builder.patch(fragment.outs, builder.emit(MATCH, label))
    return Program(ops=builder.ops, start=fragment.start, slots=builder.slots)


class _Builder:
    def __init__(self, symbolize: _Symbolizer, digit_mask: int, *, capture: bool, allow_unbounded: bool) -> None:
        self.ops: list[_Op] = []
        self.symbolize = symbolize
        self.digit_mask = digit_mask
        self.capture = capture
        self.allow_unbounded = allow_unbounded
        self.slots = 0

    def emit(self, kind: int, arg: int = 0) -> int:
        self.ops.append(_Op(kind=kind, arg=arg))
        return len(self.ops) - 1

    def patch(self, outs: Iterable[tuple[int, str]], target: int) -> None:
        for index, attribute in outs:
            self.ops[index] = replace(self.ops[index], **{attribute: target})

    def sequence(self, items: Sequence[RegexNode]) -> _Fragment:
        fragments = list(starmap(self.item, items))
        if not fragments:
            return self._empty()
        for previous, following in pairwise(fragments):
            self.patch(previous.outs, following.start)
        return _Fragment(start=fragments[0].start, outs=fragments[-1].outs)

    def item(  # ruff:ignore[too-many-return-statements, complex-structure]  # one arm per regex opcode
        self, opcode: int, argument: RegexArgument
    ) -> _Fragment:
        if opcode is RegexOp.LITERAL:
            index = self.emit(CHAR, self.symbolize(cast("int", argument)))
            return _Fragment(start=index, outs=[(index, "next")])
        if opcode is RegexOp.IN:
            index = self.emit(CHAR, self.class_mask(cast("RegexItems", argument)))
            return _Fragment(start=index, outs=[(index, "next")])
        if opcode is RegexOp.AT:
            if argument is not RegexOp.AT_END:
                msg = f"unsupported anchor {argument}"
                raise ValueError(msg)
            index = self.emit(ASSERT_END)
            return _Fragment(start=index, outs=[(index, "next")])
        if opcode is RegexOp.SUBPATTERN:
            group, _add, _remove, items = cast("tuple[int | None, int, int, RegexItems]", argument)
            if group is None or not self.capture:
                return self.sequence(items)
            open_index = self.emit(SAVE, 2 * group)
            inner = self.sequence(items)
            close_index = self.emit(SAVE, 2 * group + 1)
            self.ops[open_index] = replace(self.ops[open_index], next=inner.start)
            self.patch(inner.outs, close_index)
            self.slots = max(self.slots, 2 * group + 2)
            return _Fragment(start=open_index, outs=[(close_index, "next")])
        if opcode is RegexOp.BRANCH:
            _unused, alternatives = cast("tuple[None, list[RegexItems]]", argument)
            return self.branch([self.sequence(alternative) for alternative in alternatives])
        if opcode is RegexOp.MAX_REPEAT:
            low, high, items = cast("tuple[int, int | None, RegexItems]", argument)
            if high is None:
                if not self.allow_unbounded:
                    msg = "unbounded repeat"
                    raise ValueError(msg)
                return self.star(low, items)
            return self.repeat(low, high, items)
        msg = f"unsupported construct {opcode}"
        raise ValueError(msg)

    def class_mask(self, items: RegexItems) -> int:
        mask = 0
        for opcode, argument in items:
            if opcode is RegexOp.LITERAL:
                mask |= self.symbolize(cast("int", argument))
            elif opcode is RegexOp.RANGE:
                low, high = cast("tuple[int, int]", argument)
                for code in range(low, high + 1):
                    mask |= self.symbolize(code)
            elif opcode is RegexOp.CATEGORY and argument is RegexOp.CATEGORY_DIGIT:
                mask |= self.digit_mask
            else:
                msg = f"unsupported class item {opcode}"
                raise ValueError(msg)
        return mask

    def branch(self, alternatives: list[_Fragment]) -> _Fragment:
        # nested splits in source order: the first alternative has the highest priority
        fragment = alternatives[-1]
        for alternative in reversed(alternatives[:-1]):
            index = self.emit(SPLIT)
            self.ops[index] = replace(self.ops[index], next=alternative.start)
            self.ops[index] = replace(self.ops[index], alt=fragment.start)
            fragment = _Fragment(start=index, outs=alternative.outs + fragment.outs)
        return fragment

    def star(self, low: int, items: Sequence[RegexNode]) -> _Fragment:
        # ``x{low,}``: the mandatory copies, then a greedy loop that prefers another copy over leaving
        copies = [self.sequence(items) for _ in range(low)]
        loop_split = self.emit(SPLIT)
        body = self.sequence(items)
        self.ops[loop_split] = replace(self.ops[loop_split], next=body.start)
        self.patch(body.outs, loop_split)
        for previous, following in pairwise(copies):
            self.patch(previous.outs, following.start)
        if copies:
            self.patch(copies[-1].outs, loop_split)
            return _Fragment(start=copies[0].start, outs=[(loop_split, "alt")])
        return _Fragment(start=loop_split, outs=[(loop_split, "alt")])

    def repeat(self, low: int, high: int, items: Sequence[RegexNode]) -> _Fragment:
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
            self.ops[index] = replace(self.ops[index], next=copy.start)
            if optional_start >= 0:
                self.patch(copy.outs, optional_start)
            else:
                tail_outs += copy.outs
            tail_outs.append((index, "alt"))
            optional_start = index
        if low == 0:
            return _Fragment(start=optional_start, outs=tail_outs)
        if optional_start >= 0:
            self.patch(copies[low - 1].outs, optional_start)
            return _Fragment(start=copies[0].start, outs=tail_outs)
        return _Fragment(start=copies[0].start, outs=copies[low - 1].outs)

    def _empty(self) -> _Fragment:
        # an empty sequence is a pass-through: a SAVE with no slot effect would cost a real slot, so use a SPLIT whose
        # two arms both wait for the same patch target
        index = self.emit(SPLIT)
        return _Fragment(start=index, outs=[(index, "next"), (index, "alt")])


@dataclass(frozen=True, slots=True, kw_only=True)
class _Op:
    # One program instruction; ``next`` and ``alt`` index the program, ``arg`` is a symbol mask, a slot or a label.
    kind: int
    arg: int = 0
    next: int = -1
    alt: int = -1


@dataclass(frozen=True, slots=True, kw_only=True)
class _Fragment:
    start: int
    outs: list[tuple[int, str]] = field(default_factory=list)


def parse_regex(pattern: str) -> RegexItems:
    return _Parser(pattern).parse()


class _Parser:
    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.index = 0
        self.groups = 0

    def parse(self) -> RegexItems:
        items = self.alternatives()
        if self.index != len(self.pattern):
            msg = f"unexpected character at offset {self.index}"
            raise ValueError(msg)
        return items

    def alternatives(self) -> RegexItems:
        alternatives = [self.sequence()]
        while self.peek() == "|":
            self.index += 1
            alternatives.append(self.sequence())
        if len(alternatives) == 1:
            return alternatives[0]
        return [(RegexOp.BRANCH, (None, alternatives))]

    def sequence(self) -> RegexItems:
        items: RegexItems = []
        while (char := self.peek()) is not None and char not in ")|":
            if char in "?*+{":
                msg = f"unsupported quantifier at offset {self.index}"
                raise ValueError(msg)
            items.append(self.quantified(self.atom()))
        return items

    def quantified(self, atom: RegexNode) -> RegexNode:
        if (quantifier := self.peek()) in {"?", "*", "+"}:
            self.index += 1
            low, high = {"?": (0, 1), "*": (0, None), "+": (1, None)}[quantifier]
            return RegexOp.MAX_REPEAT, (low, high, [atom])
        if quantifier != "{":
            return atom
        self.index += 1
        low = self.number()
        if self.peek() == ",":
            self.index += 1
            high = (
                self.number()
                if (next_char := self.peek()) is not None and next_char.isascii() and next_char.isdigit()
                else None
            )
        else:
            high = low
        if self.peek() != "}" or (high is not None and high < low):
            msg = f"invalid repeat at offset {self.index}"
            raise ValueError(msg)
        self.index += 1
        return RegexOp.MAX_REPEAT, (low, high, [atom])

    def number(self) -> int:
        start = self.index
        while (char := self.peek()) is not None and char.isascii() and char.isdigit():
            self.index += 1
        if start == self.index:
            msg = f"invalid repeat at offset {self.index}"
            raise ValueError(msg)
        return int(self.pattern[start : self.index])

    def atom(self) -> RegexNode:
        char = self.take()
        if char == "(":
            group: int | None
            if self.peek() == "?":
                self.index += 1
                if self.take() != ":":
                    msg = f"unsupported group at offset {self.index}"
                    raise ValueError(msg)
                group = None
            else:
                self.groups += 1
                group = self.groups
            items = self.alternatives()
            if self.peek() != ")":
                msg = f"unclosed group at offset {self.index}"
                raise ValueError(msg)
            self.index += 1
            return RegexOp.SUBPATTERN, (group, 0, 0, items)
        if char == "[":
            return RegexOp.IN, self.character_class()
        if char == "\\":
            return self.escaped(slash_consumed=True)
        if char == "$":
            if self.peek() not in {None, "|", ")"}:
                msg = f"unsupported anchor at offset {self.index}"
                raise ValueError(msg)
            return RegexOp.AT, RegexOp.AT_END
        if char in ".^]}":
            msg = f"unsupported construct {char} at offset {self.index - 1}"
            raise ValueError(msg)
        return RegexOp.LITERAL, ord(char)

    def take(self) -> str:
        if (char := self.peek()) is None:
            msg = f"incomplete pattern at offset {self.index}"
            raise ValueError(msg)
        self.index += 1
        return char

    def character_class(self) -> RegexItems:
        if self.peek() == "^":
            msg = f"unsupported class at offset {self.index}"
            raise ValueError(msg)
        items: RegexItems = []
        while (char := self.peek()) is not None and char != "]":
            items.append(self.class_item())
        if not items or self.peek() != "]":
            msg = f"unclosed or empty class at offset {self.index}"
            raise ValueError(msg)
        self.index += 1
        return items

    def class_item(self) -> RegexNode:
        first = self.escaped(in_class=True) if self.peek() == "\\" else (RegexOp.LITERAL, ord(self.take()))
        if self.peek() != "-" or self.pattern[self.index + 1 : self.index + 2] in {"", "]"}:
            return first
        self.index += 1
        second = self.escaped(in_class=True) if self.peek() == "\\" else (RegexOp.LITERAL, ord(self.take()))
        if first[0] is not RegexOp.LITERAL or second[0] is not RegexOp.LITERAL:
            msg = f"invalid class range at offset {self.index}"
            raise ValueError(msg)
        low, high = cast("int", first[1]), cast("int", second[1])
        if low > high:
            msg = f"invalid class range at offset {self.index}"
            raise ValueError(msg)
        return RegexOp.RANGE, (low, high)

    def escaped(self, *, slash_consumed: bool = False, in_class: bool = False) -> RegexNode:
        if not slash_consumed:
            self.index += 1
        char = self.take()
        if char == "d":
            return (
                (RegexOp.CATEGORY, RegexOp.CATEGORY_DIGIT)
                if in_class
                else (RegexOp.IN, [(RegexOp.CATEGORY, RegexOp.CATEGORY_DIGIT)])
            )
        if char in "tnrfv":
            return RegexOp.LITERAL, ord({"t": "\t", "n": "\n", "r": "\r", "f": "\f", "v": "\v"}[char])
        if char.isalnum():
            msg = f"unsupported escape \\{char} at offset {self.index - 2}"
            raise ValueError(msg)
        return RegexOp.LITERAL, ord(char)

    def peek(self) -> str | None:
        return self.pattern[self.index] if self.index < len(self.pattern) else None


@dataclass(frozen=True, slots=True, kw_only=True)
class Program:
    # An ordered NFA program; ``split`` prefers ``next`` over ``alt``, which encodes Java's evaluation order.
    ops: list[_Op]
    start: int
    slots: int


def union_program(programs: Sequence[Program]) -> Program:
    # Join programs under one start so a subset construction labels each accept with its own program's label.
    ops: list[_Op] = []
    starts: list[int] = []
    slots = 0
    for program in programs:
        offset = len(ops)
        ops.extend(
            _Op(
                kind=op.kind,
                arg=op.arg,
                next=op.next + offset if op.next >= 0 else -1,
                alt=op.alt + offset if op.alt >= 0 else -1,
            )
            for op in program.ops
        )
        starts.append(program.start + offset)
        slots = max(slots, program.slots)
    start = starts[-1]
    for alternative in reversed(starts[:-1]):
        ops.append(_Op(kind=SPLIT, arg=0, next=alternative, alt=start))
        start = len(ops) - 1
    return Program(ops=ops, start=start, slots=slots)


def sticky_program(program: Program, all_symbols: int) -> Program:
    # Make each accept persist over any further symbols, so a router reports every prefix that matched so far.
    ops = [_Op(kind=op.kind, arg=op.arg, next=op.next, alt=op.alt) for op in program.ops]
    for index in [position for position, op in enumerate(ops) if op.kind == MATCH]:
        loop_split = len(ops)
        ops.extend((
            _Op(kind=SPLIT, arg=0, next=loop_split + 1, alt=loop_split + 2),
            _Op(kind=CHAR, arg=all_symbols, next=loop_split),
            _Op(kind=MATCH, arg=ops[index].arg),
        ))
        ops[index] = _Op(kind=SPLIT, arg=0, next=loop_split + 1, alt=loop_split + 2)
    return Program(ops=ops, start=program.start, slots=program.slots)


def compile_dfa(program: Program, symbols: int = DIGIT_SYMBOLS + 1, end_symbol: int = _END) -> Dfa:
    # Subset construction with labeled accepts (the OR of the reached ``MATCH`` labels), then Moore minimization.
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
    return minimize(
        Dfa(
            symbols=symbols,
            next=[rows[state] for state in range(count)],
            accepts=[accepts[state] for state in range(count)],
        )
    )


def _closure(program: Program, seeds: Iterable[int]) -> list[int]:
    # List the consuming and matching instructions reachable through epsilons, in priority order, first visit wins.
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


def _consumes(op: _Op, symbol: int, end_symbol: int = _END) -> bool:
    if op.kind == CHAR:
        return symbol != end_symbol and bool(op.arg >> symbol & 1)
    return op.kind == ASSERT_END and symbol == end_symbol


def minimize(dfa: Dfa) -> Dfa:
    # Moore partition refinement on the accept labels, keeping the dead state at 0 and the start state at 1.
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
        symbols=dfa.symbols,
        next=[[renumber[block[target]] for target in dfa.next[representative[value]]] for value in order],
        accepts=[dfa.accepts[representative[value]] for value in order],
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Dfa:
    # Row 0 is the dead state, row 1 the start state; ``accepts[state]`` is 0 when the state does not accept.
    symbols: int
    next: list[list[int]]
    accepts: list[int]


def compile_priority_dfa(program: Program, symbols: int = DIGIT_SYMBOLS + 1) -> PriorityDfa:
    # Build the ordered-thread automaton.
    #
    # A state is the priority-ordered live threads ahead of the best provisional accept plus that accept's age, so the
    # automaton reports the end Java's backtracker would return.
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
            next=[rows[state] for state in range(len(keys))],
            accept=[age >= 0 for _threads, age in keys],
            final=[_is_final(key) for key in keys],
            offset_back=[max(age, 0) for _threads, age in keys],
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
        next=[[renumber[block[target]] for target in dfa.next[representative[value]]] for value in order],
        accept=[dfa.accept[representative[value]] for value in order],
        final=[dfa.final[representative[value]] for value in order],
        offset_back=[dfa.offset_back[representative[value]] for value in order],
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PriorityDfa:
    # The ordered-thread automaton for a prefix or IDD pattern.
    #
    # Per state: ``accept`` when a provisional accept exists, ``final`` when no higher-priority thread is alive (the
    # moment Java's ``lookingAt`` returns), ``offset_back`` the digits consumed since the accepted end. A final state
    # has no outgoing edges.
    next: list[list[int]]
    accept: list[bool]
    final: list[bool]
    offset_back: list[int]


def _is_final(key: _PriorityKey) -> bool:
    threads, age = key
    return age >= 0 and not threads


def accepted_lengths(dfa: Dfa, limit: int = 20) -> dict[int, int]:
    # Map each accepted length up to ``limit`` to the OR of the labels accepting at that length.
    lengths: dict[int, int] = {}
    frontier = {1}
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
    # Measure the longest string the pattern accepts on the priority automaton's accept states.
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
    # Measure the shortest string the pattern accepts, or -1 when the language is empty.
    frontier = {1}
    for length in range(limit + 1):
        if any(dfa.accept[state] for state in frontier):
            return length
        frontier = {dfa.next[state][symbol] for state in frontier for symbol in range(DIGIT_SYMBOLS)} - {0}
        if not frontier:
            break
    return -1


def lag(dfa: PriorityDfa) -> int:
    # Measure the longest wait between a provisional accept and its finalization, the digits a walk may replay.
    return max(dfa.offset_back)


def match_end(dfa: PriorityDfa, digits: Sequence[int]) -> int:
    # Java's ``lookingAt`` end for ``digits`` followed by the end of input, or -1 when nothing matches.
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
    # Group spans ``{group: (start, end)}`` of Java's path over exactly ``digits``, or None.
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
    # Measure the largest simultaneous thread count a Pike run reaches over inputs up to ``limit`` digits.
    frontier = {tuple(_closure(program, [program.start]))}
    high = max(len(threads) for threads in frontier)
    for _ in range(limit + 1):
        successors: set[tuple[int, ...]] = set()
        for threads in frontier:
            for symbol in range(DIGIT_SYMBOLS + 1):
                if seeds := [program.ops[index].next for index in threads if _consumes(program.ops[index], symbol)]:
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
    "RegexArgument",
    "RegexItems",
    "RegexNode",
    "RegexOp",
    "accepted_lengths",
    "compile_dfa",
    "compile_priority_dfa",
    "compile_program",
    "lag",
    "longest_accept",
    "match_end",
    "max_threads",
    "minimize",
    "parse_regex",
    "pike_spans",
    "shortest_accept",
    "sticky_program",
    "union_program",
]
