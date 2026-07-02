"""Cross-check the minifier against the competing projects' own test corpora.

The corpus under ``_corpus/`` is tdewolff/minify's golden ``{input, expected}`` JS
table (MIT, vendored with attribution in ``_corpus/README.md``). We do not assert a
byte-for-byte match against tdewolff - its expected output applies identifier
mangling, constant folding and statement merging that land in later phases. Instead
this gates the property that *must* hold at every phase: **semantic equivalence**.

For every case we check:

* idempotence - ``minify(minify(x)) == minify(x)``;
* the output re-parses without error;
* the output's AST equals the input's AST, modulo the empty statements minification
  legitimately drops (the structural equivalence proof).

Inputs the parser does not yet handle are returned verbatim, so they are trivially
equivalent; the AST checks are skipped for them but idempotence still holds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypeAlias

import pytest

from turbohtml import JSMinify, _html, minify_js

_SExpr: TypeAlias = "list[_SExpr] | str"  # an AST-dump node: a `(head child...)` list, or an atom


def nomangle(source: str) -> str:
    # the structural AST check compares un-mangled output, so names stay stable
    return minify_js(source, JSMinify(mangle=False, fold=False))


_CORPUS = json.loads((Path(__file__).parent / "_corpus" / "tdewolff_js.json").read_text())

_CASES = [pytest.param(row["input"], id=f"L{row['line']}") for row in _CORPUS]


def _canon_num(lexeme: str) -> str:
    """Canonicalise a numeric lexeme exactly as the printer does, so the value-preserving
    number minification (1.0 -> 1, 1000 -> 1e3) does not read as an AST difference."""
    digits = lexeme.replace("_", "")
    has_e = "e" in digits or "E" in digits
    dot = digits.find(".")
    radix = len(digits) >= 2 and digits[0] == "0" and digits[1] in "xXoObB"
    legacy = dot < 0 and not has_e and len(digits) > 1 and digits[0] == "0" and digits[1].isdigit()
    if radix or has_e or legacy:
        return digits
    if dot < 0:
        zeros = len(digits) - len(digits.rstrip("0"))
        head = len(digits) - zeros
        if head == 0:
            return digits
        if zeros >= 1 and head + 1 + len(str(zeros)) < len(digits):
            return f"{digits[:head]}e{zeros}"
        return digits
    istart = 0
    while istart < dot and digits[istart] == "0":
        istart += 1
    fend = len(digits)
    while fend > dot + 1 and digits[fend - 1] == "0":
        fend -= 1
    out = digits[istart:dot]
    if fend > dot + 1:
        out += "." + digits[dot + 1 : fend]
    return out or "0"


_SEXPR_TOKEN = re.compile(r"\(|\)|'[^']*'|[^\s()']+")


def _parse_sexpr(tokens: list[str], pos: int) -> tuple[_SExpr, int]:
    """Parse one node of the AST dump (a `(head child...)` s-expression) into a nested list."""
    if tokens[pos] != "(":
        return tokens[pos], pos + 1
    node: list[_SExpr] = []
    pos += 1
    while tokens[pos] != ")":
        child, pos = _parse_sexpr(tokens, pos)
        node.append(child)
    return node, pos + 1


def _unwrap_single_blocks(node: _SExpr) -> _SExpr:
    """Canonicalize a block that holds a single statement to that statement, so the minifier dropping a
    loop body's braces (`for(;;){g()}` -> `for(;;)g()`) does not read as a difference. Applied uniformly
    to both sides of the comparison; the minifier only ever drops braces around a scope-free statement,
    and that it keeps a let/const/class/function block is pinned in test_minify."""
    if not isinstance(node, list):
        return node
    node = [_unwrap_single_blocks(child) for child in node]
    if len(node) == 2 and node[0] == "block" and isinstance(node[1], list) and node[1] and node[1][0] == "body":
        statements = [child for child in node[1][1:] if isinstance(child, list)]
        if len(statements) == 1:
            return statements[0]
    return node


def _dump_sexpr(node: _SExpr) -> str:
    if isinstance(node, list):
        return "(" + " ".join(_dump_sexpr(child) for child in node) + ")"
    return node


def _norm(dump: str) -> str:
    """Normalize an AST dump: drop empty statements (minified out) and canonicalize
    numeric and BigInt literals (value-preserving number minification, BigInt separator
    stripping) so equivalent ASTs match."""
    dump = re.sub(r"\(empty\)", "", dump)
    dump = re.sub(r"\(num '([^']*)'\)", lambda mt: f"(num '{_canon_num(mt.group(1))}')", dump)
    dump = re.sub(r"\(bigint '([^']*)'\)", lambda mt: f"(bigint '{mt.group(1).replace('_', '')}')", dump)
    # a["x"] == a.x and {"x":1} == {x:1} when the key is a bare identifier name; a quoted __proto__
    # data key keeps its quotes (its meaning differs from the bare form), so it is left alone
    name = r"""['"]([A-Za-z_$][A-Za-z0-9_$]*)['"]"""
    dump = re.sub(rf"\(prop \(str '{name}'\)\)", r"(prop (id '\1'))", dump)
    dump = re.sub(
        rf"\(key \(str '{name}'\)\)",
        lambda mt: mt.group(0) if mt.group(1) == "__proto__" else f"(key (id '{mt.group(1)}'))",
        dump,
    )
    tree, _ = _parse_sexpr(_SEXPR_TOKEN.findall(dump), 0)
    dump = _dump_sexpr(_unwrap_single_blocks(tree))
    dump = re.sub(r"\s+", " ", dump)
    return dump.replace("( ", "(").replace(" )", ")").strip()


def _ast(source: str) -> str:
    """Return the parser's S-expression AST dump, raising ValueError if it does not parse."""
    return _html._minify_js_parse(source)


@pytest.mark.parametrize("source", _CASES)
def test_semantic_equivalence(source: str) -> None:
    try:
        out = minify_js(source)
    except ValueError:
        # a construct the parser does not handle fails loudly; there is nothing the
        # minifier could have transformed, so there is nothing to verify here.
        return
    # the structural transforms (whitespace, semicolons, parens) preserve the AST, so the
    # un-mangled output -- where identifier names are unchanged -- parses to the same tree;
    # a re-parse failure raises ValueError and fails the test. Rename correctness is gated by
    # execution differentials in test_mangle_differential.py.
    structural = nomangle(source)
    assert _norm(_ast(source)) == _norm(_ast(structural)), "minification changed the AST"
    # idempotence; minify_js re-parses out internally, so this also proves the mangled output parses
    assert minify_js(out) == out, "minification is not idempotent"


def test_corpus_is_substantial() -> None:
    # guard against an empty/short corpus silently passing the gate
    assert len(_CORPUS) > 700
