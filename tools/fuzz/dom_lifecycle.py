"""Lifecycle byte programs retain wrappers across callbacks and reentry, then check native tree invariants."""

from __future__ import annotations

import gc
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Final, NamedTuple, cast

from typing_extensions import override

from turbohtml import (
    Element,
    IncrementalParser,
    Node,
    NodeFilter,
    NodeIterator,
    Range,
    Text,
    Tokenizer,
    TreeWalker,
    parse,
)
from turbohtml._html import _tree_verify  # ruff: ignore[import-private-name] - the fuzz-only native invariant hook
from turbohtml.migration.markupsafe import escape
from turbohtml.mutations import MutationObserver
from turbohtml.rewrite import rewrite
from turbohtml.saxparse import SaxHandler, sax_parse

if TYPE_CHECKING:
    import random
    from collections.abc import Iterable, Iterator, Sequence

    from turbohtml.mutations import MutationRecord
    from turbohtml.rewrite import Element as RewriteHandle

# libxml2's api.c bounds content and copies (MAX_CONTENT 100, MAX_COPY_NODES 50) so a short input cannot build an
# unbounded tree; these keep the dom-program input bound and cap execution, nesting, registers and created nodes
_MAX_BYTES: Final[int] = 1024
_SLOTS: Final[int] = 8
_MAX_NODES: Final[int] = 48
_WORDS: Final[tuple[str, ...]] = ("", "alpha", "beta gamma")
_MARKUP: Final[tuple[str, ...]] = (
    "<p>a<b>c</b></p>",
    "<div id=x><span class=y>t</span><!--c--></div>",
    "<table><td>x<td>y</table>",
    "<template><p>t</p></template><i>z</i>",
)
_EXPECTED: Final[tuple[type[Exception], ...]] = (ValueError, TypeError, IndexError, RuntimeError)


class LifecycleStep(NamedTuple):
    # One executed instruction: its nesting depth, operation, outcome and native invariant violations.
    depth: int
    operation: str
    outcome: str
    violations: tuple[int, ...]


def dom_lifecycle_generate(rng: random.Random) -> str:
    # Seed every register ring before random instructions.
    return (_PREFIX + bytes(rng.randrange(256) for _ in range(rng.randrange(1, 65) * 4))).hex()


def dom_lifecycle_controls() -> dict[str, bool]:
    # Require a reported violation and a drifting replay to fail the registered checker.
    seed: Final = dom_lifecycle_seeds()[0]
    broken: Final = ((LifecycleStep(0, "element", "ok", (1, 0, 0, 0)),), ())
    replays: Final = iter((((), ("<p></p>",)), ((), ("<div></div>",))))
    return {
        "missing violation": dom_lifecycle_check(seed, lambda _program: broken) is not None,
        "replay drift": dom_lifecycle_check(seed, lambda _program: next(replays)) is not None,
    }


def dom_lifecycle_seeds() -> list[str]:
    # Run every opcode on each slot with a mutating nested program, then fire the callbacks it registered.
    return [
        (
            _PREFIX + bytes((opcode, slot, slot + 9, len(body) // 4 | slot % 3 << 2 | slot % 2 << 4)) + body + _TRAILER
        ).hex()
        for opcode in range(len(_OPERATIONS))
        for slot in range(_SLOTS)
        for body in (_BODIES[slot // 3 % len(_BODIES)],)
    ]


def dom_lifecycle_check(
    text: str, run: Callable[[bytes], tuple[tuple[LifecycleStep, ...], tuple[str, ...]]] | None = None
) -> str | None:
    # Require clean native invariants after every step and an identical replay.
    if len(text) > _MAX_BYTES * 2:
        msg: Final = "DOM lifecycle program exceeds the input limit"
        raise UnsupportedDomLifecycleError(msg)
    try:
        program: Final = bytes.fromhex(text)
    except ValueError as error:
        msg: Final = "DOM lifecycle program must contain hexadecimal bytes"
        raise UnsupportedDomLifecycleError(msg) from error
    runner: Final = run or run_dom_lifecycle
    first: Final = runner(program)
    if broken := next((step for step in first[0] if any(step.violations)), None):
        return f"tree invariant broken after {broken.operation}"
    return None if runner(program) == first else "DOM lifecycle program replays differently"


class UnsupportedDomLifecycleError(ValueError):
    # Reject unsupported encodings before allocating register state.
    ...


def run_dom_lifecycle(program: bytes) -> tuple[tuple[LifecycleStep, ...], tuple[str, ...]]:
    # Execute the program and return each step plus the final markup of every node register.
    machine: Final = _Machine(_instructions(program))
    machine.block(range(len(machine.code)))
    return tuple(machine.trace), tuple("" if node is None else node.serialize() for node in machine.nodes)


def _instructions(program: bytes) -> list[tuple[int, int, int, int]]:
    bounded: Final = program[:_MAX_BYTES]
    return [
        (bounded[index], bounded[index + 1], bounded[index + 2], bounded[index + 3])
        for index in range(0, len(bounded) - len(bounded) % 4, 4)
    ]


def dom_lifecycle_race(seeds: Sequence[str], *, shared: bool = False) -> list[tuple[LifecycleStep, ...]]:
    # Run every seed in its own thread at once, so the programs contend on the module and interpreter state.
    #
    # With ``shared`` set, every program reads and writes one set of node registers, so the threads also edit, move and
    # walk the same trees.
    nodes: Final[list[Node | None] | None] = [None] * _SLOTS if shared else None
    machines: Final = [_Machine(_instructions(bytes.fromhex(seed)), nodes) for seed in seeds]
    start: Final = threading.Barrier(len(machines))

    def run(machine: _Machine) -> None:
        start.wait()
        machine.block(range(len(machine.code)))

    threads: Final = [threading.Thread(target=run, args=(machine,)) for machine in machines]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return [tuple(machine.trace) for machine in machines]


class _UnwindError(Exception):
    # Unwind a nested program through the native caller that invoked its callback.
    ...


class _Machine:
    # Typed register rings over one program; operands pick slots, so overwriting a slot drops its reference.
    def __init__(self, code: list[tuple[int, int, int, int]], nodes: list[Node | None] | None = None) -> None:
        self.code: Final = code
        self.nodes: Final[list[Node | None]] = [None] * _SLOTS if nodes is None else nodes
        self.walkers: Final[list[TreeWalker | NodeIterator | Iterator[object] | None]] = [None] * _SLOTS
        self.parsers: Final[list[Tokenizer | IncrementalParser | None]] = [None] * _SLOTS
        self.observers: Final[list[MutationObserver | None]] = [None] * _SLOTS
        self.trace: Final[list[LifecycleStep]] = []
        self.depth = 0
        self.created = 0

    def nested(self, body: range) -> None:
        # Run a callback's program one level deeper, so reentry stays bounded by depth instead of the stack.
        if self.depth < 2:
            self.depth += 1
            try:
                self.block(body)
            finally:
                self.depth -= 1

    def block(self, span: range) -> None:
        index = span.start
        max_steps = 256
        while index < span.stop and len(self.trace) < max_steps:
            opcode, first, second, third = self.code[index]
            name, operation, nested = _OPERATIONS[opcode % len(_OPERATIONS)]
            body = range(index + 1, min(index + 1 + third % 4, span.stop)) if nested else range(index + 1, index + 1)
            if operation is None and self.depth:
                raise _UnwindError
            try:
                outcome = "abort" if operation is None else operation(self, first % _SLOTS, second, third, body)
            except (*_EXPECTED, _UnwindError) as error:
                outcome = type(error).__name__
            self.trace.append(LifecycleStep(self.depth, name, outcome, self.verify()))
            index = body.stop

    def verify(self) -> tuple[int, ...]:
        roots: Final = [
            *(node for node in self.nodes if node is not None),
            *(walker.root for walker in self.walkers if isinstance(walker, (TreeWalker, NodeIterator))),
        ]
        return tuple(map(sum, zip((0, 0, 0, 0), *(_tree_verify(node) for node in roots), strict=True)))


def _element(machine: _Machine, slot: int, second: int, _third: int, _body: range) -> str:
    if machine.created >= _MAX_NODES:
        return "cap"
    machine.created += 1
    tags = ("p", "div", "span", "template", "b")
    machine.nodes[slot] = Element(tags[second % len(tags)])
    return "ok"


def _text(machine: _Machine, slot: int, second: int, _third: int, _body: range) -> str:
    if machine.created >= _MAX_NODES:
        return "cap"
    machine.created += 1
    machine.nodes[slot] = Text(_WORDS[second % len(_WORDS)])
    return "ok"


def _parse(machine: _Machine, slot: int, second: int, _third: int, _body: range) -> str:
    if machine.created >= _MAX_NODES:
        return "cap"
    machine.created += 8
    machine.nodes[slot] = parse(_MARKUP[second % len(_MARKUP)])
    return "ok"


def _navigate(machine: _Machine, slot: int, second: int, third: int, _body: range) -> str:
    if (source := machine.nodes[second % _SLOTS]) is None:
        return "empty"
    children: Final = source.children
    relatives: Final = (
        source.parent,
        children[0] if children else None,
        children[-1] if children else None,
        source.next_sibling,
        source.previous_sibling,
    )
    machine.nodes[slot] = relatives[third % len(relatives)]
    return "ok"


def _append(machine: _Machine, slot: int, second: int, _third: int, _body: range) -> str:
    target: Final = machine.nodes[slot]
    if not isinstance(target, Element) or (child := machine.nodes[second % _SLOTS]) is None:
        return "empty"
    target.append(child)
    return "ok"


def _insert(machine: _Machine, slot: int, second: int, third: int, body: range) -> str:
    target: Final = machine.nodes[slot]
    if not isinstance(target, Element) or (child := machine.nodes[second % _SLOTS]) is None:
        return "empty"
    target.insert(cast("int", _Index(machine, body, third, second % 3)), child)
    return "ok"


def _extract(machine: _Machine, slot: int, _second: int, _third: int, _body: range) -> str:
    if (node := machine.nodes[slot]) is None:
        return "empty"
    node.extract()
    return "ok"


def _decompose(machine: _Machine, slot: int, _second: int, _third: int, _body: range) -> str:
    if (node := machine.nodes[slot]) is None:
        return "empty"
    node.decompose()
    return "ok"


def _unwrap(machine: _Machine, slot: int, _second: int, _third: int, _body: range) -> str:
    if (node := machine.nodes[slot]) is None:
        return "empty"
    node.unwrap()
    return "ok"


def _replace(machine: _Machine, slot: int, second: int, _third: int, _body: range) -> str:
    if (node := machine.nodes[slot]) is None or (other := machine.nodes[second % _SLOTS]) is None:
        return "empty"
    node.replace_with(other)
    return "ok"


def _normalize(machine: _Machine, slot: int, _second: int, _third: int, _body: range) -> str:
    if not isinstance(node := machine.nodes[slot], Element):
        return "empty"
    node.normalize()
    return "ok"


def _range_extract(machine: _Machine, slot: int, second: int, _third: int, _body: range) -> str:
    if (node := machine.nodes[slot]) is None:
        return "empty"
    selection: Final = Range(node)
    selection.select_node_contents(node)
    machine.nodes[second % _SLOTS] = selection.extract_contents()
    return "ok"


def _collect(machine: _Machine, slot: int, second: int, _third: int, _body: range) -> str:
    if second % 4 == 0:
        machine.nodes[slot] = None
    elif second % 4 == 1:
        machine.walkers[slot] = None
    elif second % 4 == 2:
        machine.parsers[slot] = None
    else:
        machine.observers[slot] = None
    gc.collect()
    return "ok"


def _tree_walker(machine: _Machine, slot: int, second: int, third: int, body: range) -> str:
    if (root := machine.nodes[second % _SLOTS]) is None:
        return "empty"
    machine.walkers[slot] = TreeWalker(root, filter=_filter(machine, body, third))
    return "ok"


def _node_iterator(machine: _Machine, slot: int, second: int, third: int, body: range) -> str:
    if (root := machine.nodes[second % _SLOTS]) is None:
        return "empty"
    machine.walkers[slot] = NodeIterator(root, filter=_filter(machine, body, third))
    return "ok"


def _filter(machine: _Machine, body: range, third: int) -> Callable[[Node], int]:
    verdicts: Final = (NodeFilter.FILTER_ACCEPT, NodeFilter.FILTER_REJECT, NodeFilter.FILTER_SKIP)

    def verdict(_node: Node) -> int:
        machine.nested(body)
        # the walker converts the verdict inside its step, so an __index__ program runs mid-traversal
        return (
            cast("int", _Index(machine, body, third, (third >> 2) % 3))
            if third >> 4 & 1
            else verdicts[(third >> 2) % 3]
        )

    return verdict


def _iterate(machine: _Machine, slot: int, second: int, third: int, _body: range) -> str:
    if (node := machine.nodes[second % _SLOTS]) is None:
        return "empty"
    iterators: Final = (node.descendants, node.iter_elements(), node.serialize_iter())
    machine.walkers[slot] = iter(iterators[third % len(iterators)])
    return "ok"


def _step(machine: _Machine, slot: int, second: int, third: int, _body: range) -> str:
    walker: Final = machine.walkers[slot]
    if isinstance(walker, TreeWalker):
        moves: Final = (
            walker.next_node,
            walker.previous_node,
            walker.parent_node,
            walker.first_child,
            walker.last_child,
            walker.next_sibling,
            walker.previous_sibling,
        )
        found: object = moves[second % len(moves)]()
    elif isinstance(walker, NodeIterator):
        found = walker.next_node() if second % 2 else walker.previous_node()
    elif walker is None:
        return "empty"
    else:
        found = next(walker, None)
    if isinstance(found, Node):
        machine.nodes[third % _SLOTS] = found
    return "ok" if found is not None else "end"


def _observe(machine: _Machine, slot: int, second: int, third: int, body: range) -> str:
    if (target := machine.nodes[second % _SLOTS]) is None:
        return "empty"

    def callback(_records: list[MutationRecord], _observer: MutationObserver) -> None:
        machine.nested(body)

    observer: Final = MutationObserver(callback)
    machine.observers[slot] = observer
    observer.observe(
        target,
        child_list=True,
        attributes=True,
        character_data=True,
        subtree=True,
        attribute_filter=cast("Sequence[str]", _Items(machine, body, third, second % 3, ["id"])),
    )
    return "ok"


def _deliver(machine: _Machine, slot: int, second: int, _third: int, _body: range) -> str:
    if (observer := machine.observers[slot]) is None:
        return "empty"
    actions: Final = (observer.deliver, observer.take_records, observer.disconnect)
    actions[second % len(actions)]()
    return "ok"


def _rewrite(machine: _Machine, slot: int, second: int, _third: int, body: range) -> str:
    def handler(handle: RewriteHandle) -> None:
        machine.nested(body)
        # the handle is callback-scoped, so the program edits it here and never stores it in a register
        if second % 3 == 0:
            handle.remove()
        elif second % 3 == 1:
            handle.after("<i>z</i>", html=True)
        else:
            handle.before("y")

    rewrite(_MARKUP[slot % len(_MARKUP)], elements=[("*", handler)], text=handler, comments=handler)
    return "ok"


def _sax(machine: _Machine, slot: int, _second: int, _third: int, body: range) -> str:
    sax_parse(_MARKUP[slot % len(_MARKUP)], _Sax(machine, body))
    return "ok"


def _tokenizer(machine: _Machine, slot: int, _second: int, _third: int, _body: range) -> str:
    machine.parsers[slot] = Tokenizer()
    return "ok"


def _incremental(machine: _Machine, slot: int, _second: int, _third: int, _body: range) -> str:
    machine.parsers[slot] = IncrementalParser()
    return "ok"


def _parser_call(machine: _Machine, slot: int, second: int, third: int, body: range) -> str:
    parser: Final = machine.parsers[slot]
    markup: Final = _MARKUP[third % len(_MARKUP)]
    if isinstance(parser, Tokenizer):
        if second % 4 == 0:
            machine.walkers[third % _SLOTS] = parser.feed(markup)
        elif second % 4 == 1:
            machine.walkers[third % _SLOTS] = parser.close()
        elif second % 4 == 2:
            parser.reset()
        else:
            # a handler whose program feeds, closes or resets this tokenizer reenters it mid-dispatch
            parser.dispatch(_Dispatch(machine, body))
    elif isinstance(parser, IncrementalParser):
        if second % 2:
            parser.feed(markup)
        else:
            machine.nodes[third % _SLOTS] = parser.close()
    else:
        return "empty"
    return "ok"


def _attribute(machine: _Machine, slot: int, second: int, third: int, body: range) -> str:
    if not isinstance(node := machine.nodes[slot], Element):
        return "empty"
    if second % 3 == 0:
        node.attrs["id"] = _WORDS[third % len(_WORDS)]
    elif second % 3 == 1:
        node.attrs.pop("id", None)
    else:
        # dict(mapping) hashes each key and the comparison calls __eq__, both from inside the attrs view
        return str(node.attrs == _Items(machine, body, third, second // 3 % 3, ["id"]))
    return "ok"


def _text_conversion(machine: _Machine, slot: int, second: int, third: int, body: range) -> str:
    if not isinstance(node := machine.nodes[slot], Element):
        return "empty"
    node.text = escape(_Text(machine, body, third, second % 3))
    return "ok"


def _extend(machine: _Machine, slot: int, second: int, third: int, body: range) -> str:
    if not isinstance(node := machine.nodes[slot], Element) or (child := machine.nodes[second % _SLOTS]) is None:
        return "empty"
    node.extend(cast("Iterable[Node]", _Items(machine, body, third, second % 3, [child])))
    return "ok"


def _compare(machine: _Machine, slot: int, second: int, third: int, body: range) -> str:
    if (node := machine.nodes[slot]) is None:
        return "empty"
    # a Node compares only with a Node, so Python falls back to the reflected __eq__ of the other operand
    return str(node == _Key(machine, body, third, second % 3))


def _select(machine: _Machine, slot: int, second: int, _third: int, _body: range) -> str:
    if not isinstance(node := machine.nodes[slot], Element):
        return "empty"
    # both populate per-handle caches stamped with tree versions, which the verifier then cross-checks
    return str(len(node.select("b, span")) if second % 2 else node.css_path())


class _Conversion:
    # An argument whose conversion method runs a nested program, then answers, raises or behaves plainly.
    def __init__(self, machine: _Machine, body: range, third: int, mode: int) -> None:
        self.machine: Final = machine
        self.body: Final = body
        self.third: Final = third
        self.mode: Final = mode

    def convert(self) -> None:
        if self.mode:
            self.machine.nested(self.body)
        if self.mode == 2:
            raise _UnwindError


class _Index(_Conversion):
    def __index__(self) -> int:
        self.convert()
        return self.third


class _Text(_Conversion):
    def __str__(self) -> str:
        self.convert()
        return _WORDS[self.third % len(_WORDS)]


class _Key(_Conversion):
    def __hash__(self) -> int:
        self.convert()
        return hash("id")

    def __eq__(self, other: object) -> bool:
        self.convert()
        return other == "id"


class _Items(_Conversion):
    # Serve as an iterable of fixed items, and as a mapping keyed by a conversion key.
    def __init__(self, machine: _Machine, body: range, third: int, mode: int, items: list[Node] | list[str]) -> None:
        super().__init__(machine, body, third, mode)
        self.items: Final = items

    def __iter__(self) -> Iterator[Node | str]:
        self.convert()
        return iter(self.items)

    def __getitem__(self, _key: object) -> str:
        return _WORDS[self.third % len(_WORDS)]

    def keys(self) -> list[_Key]:
        return [_Key(self.machine, self.body, self.third, self.mode)]


class _Sax(SaxHandler):
    # A sax_parse handler whose start and text events run the nested program.
    def __init__(self, machine: _Machine, body: range) -> None:
        self.machine: Final = machine
        self.body: Final = body

    @override
    def start_element(self, tag: str, attrs: tuple[tuple[str, str | None], ...]) -> None:
        self.machine.nested(self.body)

    @override
    def characters(self, data: str) -> None:
        self.machine.nested(self.body)


class _Dispatch:
    # A Tokenizer.dispatch handler whose every html.parser event runs the nested program.
    def __init__(self, machine: _Machine, body: range) -> None:
        self.machine: Final = machine
        self.body: Final = body

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *_args: self.machine.nested(self.body)


_Operation = Callable[[_Machine, int, int, int, range], str]
_OPERATIONS: Final[tuple[tuple[str, _Operation | None, bool], ...]] = (
    ("element", _element, False),
    ("text", _text, False),
    ("parse", _parse, False),
    ("navigate", _navigate, False),
    ("append", _append, False),
    ("insert", _insert, True),
    ("extract", _extract, False),
    ("decompose", _decompose, False),
    ("unwrap", _unwrap, False),
    ("replace", _replace, False),
    ("collect", _collect, False),
    ("tree-walker", _tree_walker, True),
    ("node-iterator", _node_iterator, True),
    ("iterate", _iterate, False),
    ("step", _step, False),
    ("observe", _observe, True),
    ("deliver", _deliver, False),
    ("rewrite", _rewrite, True),
    ("sax", _sax, True),
    ("tokenizer", _tokenizer, False),
    ("incremental", _incremental, False),
    ("parser-call", _parser_call, True),
    ("attribute", _attribute, True),
    ("text-conversion", _text_conversion, True),
    ("extend", _extend, True),
    ("compare", _compare, True),
    ("select", _select, False),
    ("raise", None, False),
    ("normalize", _normalize, False),
    ("range-extract", _range_extract, False),
)

# element p, element div, text, parsed document, then html/body/p/b of that document, a tokenizer and a fed parser
_PREFIX: Final[bytes] = bytes((
    *(0, 0, 0, 0),
    *(0, 1, 1, 0),
    *(1, 2, 1, 0),
    *(2, 3, 0, 0),
    *(4, 0, 1, 0),
    *(4, 0, 2, 0),
    *(3, 4, 3, 1),
    *(3, 5, 4, 2),
    *(3, 6, 5, 1),
    *(3, 7, 6, 2),
    *(19, 0, 0, 0),
    *(20, 1, 0, 0),
    *(21, 1, 1, 0),
))
# a nested program edits across trees, reenters the tokenizer and incremental parser, or runs a program of its own
_BODIES: Final[tuple[bytes, ...]] = (
    bytes((*(6, 1, 0, 0), *(4, 6, 1, 0), *(7, 7, 0, 0))),
    bytes((*(21, 0, 0, 0), *(21, 0, 2, 0), *(21, 1, 0, 2))),
    bytes((*(18, 0, 0, 2), *(7, 7, 0, 0), *(8, 6, 0, 0))),
)
# step every walker, mutate the observed trees, deliver every observer, then dispatch the tokenizer into a program
# that feeds and resets it
_TRAILER: Final[bytes] = bytes((
    *(byte for slot in range(_SLOTS) for byte in (14, slot, slot, slot)),
    *(22, 0, 0, 1),
    *(5, 6, 2, 0),
    *(byte for slot in range(_SLOTS) for byte in (16, slot, 0, 0)),
    *(21, 0, 3, 3),
    *_BODIES[1],
))


__all__: Final[list[str]] = [
    "LifecycleStep",
    "UnsupportedDomLifecycleError",
    "dom_lifecycle_check",
    "dom_lifecycle_controls",
    "dom_lifecycle_generate",
    "dom_lifecycle_race",
    "dom_lifecycle_seeds",
    "run_dom_lifecycle",
]
