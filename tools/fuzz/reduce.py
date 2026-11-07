"""Structural deletion keeps language constructs intact before character reduction."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypedDict, cast

from tinycss2 import parse_blocks_contents, parse_component_value_list, parse_stylesheet, serialize
from tinycss2.ast import AtRule, Declaration, ParseError, QualifiedRule

from turbohtml import Element, parse, parse_fragment

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import TextIO

    from tinycss2.ast import Node

_NODE_DIR: Final = Path(__file__).resolve().parents[1] / "bench" / "node"


def minimize(
    text: str, reproduces: Callable[[str], bool], syntax: Literal["html", "css", "js"], budget: int = 600
) -> str:
    # Accept shorter candidates only when the original finding predicate holds.
    if budget <= 0:
        return text
    calls = 0

    def check(candidate: str) -> bool:
        nonlocal calls
        if calls >= budget:
            return False
        calls += 1
        return reproduces(candidate)

    if syntax == "js":
        return _js(text, check, budget)
    candidates = _html if syntax == "html" else _css
    while calls < budget:
        for candidate in candidates(text):
            if len(candidate) < len(text) and check(candidate):
                text = candidate
                break
        else:
            break
    return text


def _js(text: str, reproduces: Callable[[str], bool], budget: int) -> str:
    if (node := shutil.which("node")) is None or not (_NODE_DIR / "node_modules" / "uglify-js").is_dir():
        msg = f"node and npm ci in {_NODE_DIR} are required for JS reduction"
        raise FileNotFoundError(msg)
    with subprocess.Popen(
        [node, str(Path(__file__).with_name("js_reduce_runner.js"))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={key: value for key, value in os.environ.items() if key not in {"LD_PRELOAD", "DYLD_INSERT_LIBRARIES"}},
    ) as process:
        stdin = cast("TextIO", process.stdin)
        stdout = cast("TextIO", process.stdout)
        stdin.write(json.dumps({"text": text, "budget": budget}) + "\n")
        stdin.flush()
        while line := stdout.readline():
            reply = cast("_Reply", json.loads(line))
            if reply["kind"] == "done":
                return reply["text"]
            stdin.write(f"{json.dumps(reproduces(reply['text']))}\n")
            stdin.flush()
    msg = f"JS reducer exited without a result: {process.returncode}"
    raise RuntimeError(msg)


def _html(text: str) -> Iterator[str]:
    for root, inner in ((parse(text), False), (parse_fragment(text), True)):
        for index, node in enumerate(list(root.descendants)):
            replacement = parse_fragment(text) if inner else parse(text)
            list(replacement.descendants)[index].extract()
            yield replacement.serialize(inner=inner)
            if isinstance(node, Element):
                for name in node.attrs:
                    replacement = parse_fragment(text) if inner else parse(text)
                    del cast("Element", list(replacement.descendants)[index]).attrs[name]
                    yield replacement.serialize(inner=inner)


def _css(text: str) -> Iterator[str]:
    yield from _css_delete(cast("list[Node]", parse_stylesheet(text)))
    yield from _css_delete(cast("list[Node]", parse_blocks_contents(text)))


def _css_delete(nodes: list[Node]) -> Iterator[str]:
    if any(isinstance(node, ParseError) for node in nodes):
        return
    for index, node in enumerate(nodes):
        if isinstance(node, Declaration):
            yield serialize(nodes[:index] + nodes[index + 1 :])
        elif isinstance(node, (AtRule, QualifiedRule)) and node.content is not None:
            content = node.content
            for candidate in _css_delete(cast("list[Node]", parse_blocks_contents(content))):
                node.content = parse_component_value_list(candidate)
                yield serialize(nodes)
            node.content = content


class _Reply(TypedDict):
    kind: Literal["candidate", "done"]
    text: str


__all__ = [
    "minimize",
]
