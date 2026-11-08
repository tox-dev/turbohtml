from __future__ import annotations

import ast
import inspect
import sys
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple

import pytest

import turbohtml._html as html

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import ModuleType

    from pytest_mock import MockerFixture

_STUB_DIR: Final[Path] = Path(__file__).parents[1] / "src" / "turbohtml" / "_stubs"


class _Default(Enum):
    NONE = auto()
    ANY = auto()


_NO_DEFAULT: Final[_Default] = _Default.NONE
_ANY_DEFAULT: Final[_Default] = _Default.ANY
_POSITIONAL: Final[frozenset[inspect._ParameterKind]] = frozenset({
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
})


class _Param(NamedTuple):
    name: str
    kind: inspect._ParameterKind
    default: _Default | str


class _Shape(NamedTuple):
    positional: list[_Param]
    keyword: dict[str, _Default | str]
    var_positional: bool
    var_keyword: bool


def _shape(params: list[_Param]) -> _Shape:
    return _Shape(
        positional=[param for param in params if param.kind in _POSITIONAL],
        keyword={p.name: p.default for p in params if p.kind is inspect.Parameter.KEYWORD_ONLY},
        var_positional=any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params),
        var_keyword=any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params),
    )


def _stub_default(node: ast.expr | None) -> _Default | str:
    if node is None:
        return _NO_DEFAULT
    if isinstance(node, ast.Constant) and node.value is Ellipsis:
        return _ANY_DEFAULT
    return ast.unparse(node)


def _stub_params(func: ast.FunctionDef, *, drop_self: bool) -> list[_Param]:
    args = func.args
    positional = [*args.posonlyargs, *args.args]
    padded = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    kinds = [inspect.Parameter.POSITIONAL_ONLY] * len(args.posonlyargs) + [
        inspect.Parameter.POSITIONAL_OR_KEYWORD
    ] * len(args.args)
    params = [
        _Param(arg.arg, kind, _stub_default(default))
        for arg, kind, default in zip(positional, kinds, padded, strict=True)
    ]
    if args.vararg is not None:
        params.append(_Param(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL, _NO_DEFAULT))
    params.extend(
        _Param(arg.arg, inspect.Parameter.KEYWORD_ONLY, _stub_default(default))
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
    )
    if args.kwarg is not None:
        params.append(_Param(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD, _NO_DEFAULT))
    if drop_self and params and params[0].name in frozenset({"self", "cls"}):
        params.pop(0)
    return params


def _runtime_params(signature: inspect.Signature) -> list[_Param]:
    return [
        _Param(name, param.kind, _NO_DEFAULT if param.default is inspect.Parameter.empty else repr(param.default))
        for name, param in signature.parameters.items()
    ]


def _is_property(method: ast.FunctionDef) -> bool:
    accessors = {"property", "setter", "deleter"}
    return any(isinstance(deco, ast.Name) and deco.id in accessors for deco in method.decorator_list) or any(
        isinstance(deco, ast.Attribute) and deco.attr in accessors for deco in method.decorator_list
    )


def _stub_entries() -> Iterator[tuple[str, ast.FunctionDef, bool]]:
    # Yield (dotted name, function node, drop_self) for every callable the stubs declare.
    for stub in sorted(_STUB_DIR.glob("*.pyi")):
        for node in ast.parse(stub.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef):
                yield node.name, node, False
            elif isinstance(node, ast.ClassDef):
                for method in node.body:
                    if not isinstance(method, ast.FunctionDef) or _is_property(method):
                        continue
                    yield (node.name if method.name == "__init__" else f"{node.name}.{method.name}"), method, True


def _resolve(name: str) -> ModuleType | Callable[..., object] | None:
    obj: ModuleType | Callable[..., object] | None = html
    for part in name.split("."):
        obj = getattr(obj, part, None)
    return obj


def _runtime_signature(obj: ModuleType | Callable[..., object] | None) -> inspect.Signature | None:
    if not callable(obj) or getattr(obj, "__text_signature__", None) is None:
        return None
    try:
        return inspect.signature(obj)
    except (ValueError, TypeError):
        # older CPython rejects some C text signatures its parser cannot model; skip them on that version
        return None


def _is_dunder(name: str) -> bool:
    leaf = name.rsplit(".", 1)[-1]
    return leaf.startswith("__") and leaf.endswith("__")


_ENTRIES: Final[dict[str, tuple[ast.FunctionDef, bool]]] = {
    name: (node, drop_self) for name, node, drop_self in _stub_entries()
}
_COMPARABLE: Final[list[str]] = sorted(
    name for name in _ENTRIES if not _is_dunder(name) and _runtime_signature(_resolve(name)) is not None
)


@pytest.mark.parametrize("name", [pytest.param(name, id=name) for name in _COMPARABLE])
def test_stub_signature_matches_runtime(name: str) -> None:
    node, drop_self = _ENTRIES[name]
    runtime = _runtime_signature(_resolve(name))
    assert runtime is not None
    stub = _shape(_stub_params(node, drop_self=drop_self))
    live = _shape(_runtime_params(runtime))

    assert len(stub.positional) == len(live.positional), "positional arity drifted"
    for stub_param, live_param in zip(stub.positional, live.positional, strict=True):
        _assert_default(stub_param.default, live_param.default)
        # a positional-only name is synthesized by C; pin it only where both sides make it keyword-addressable
        keyword_addressable: Final = stub_param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD is live_param.kind
        assert not keyword_addressable or stub_param.name == live_param.name, (
            "a keyword-addressable positional was renamed"
        )

    assert stub.var_positional == live.var_positional, "*args presence drifted"
    assert stub.var_keyword == live.var_keyword, "**kwargs presence drifted"
    assert set(live.keyword) <= set(stub.keyword), "the runtime exposes a keyword the stub omits"
    assert live.var_keyword or set(stub.keyword) == set(live.keyword), "the stub declares a keyword the runtime lacks"
    for keyword, live_default in live.keyword.items():
        _assert_default(stub.keyword[keyword], live_default)


def _assert_default(stub_default: _Default | str, live_default: _Default | str) -> None:
    assert (stub_default is _NO_DEFAULT) == (live_default is _NO_DEFAULT), "a default was added or dropped"
    if stub_default not in {_NO_DEFAULT, _ANY_DEFAULT}:
        assert stub_default == live_default, "a concrete default value drifted"


@pytest.mark.parametrize("name", [pytest.param(name, id=name) for name in sorted(_ENTRIES)])
def test_every_stub_callable_exists_in_the_extension(name: str) -> None:
    assert _resolve(name) is not None, f"{name} is declared in the stubs but missing from turbohtml._html"


@pytest.mark.parametrize(
    "name",
    [pytest.param("NotAnExtensionMember", id="top-level"), pytest.param("Document.not_a_method", id="attribute")],
)
def test_resolve_reports_a_name_the_extension_lacks(name: str) -> None:
    assert _resolve(name) is None


def test_runtime_signature_ignores_a_non_callable() -> None:
    assert _runtime_signature(42) is None  # ty: ignore[invalid-argument-type]  # passes a non-callable on purpose


def test_runtime_signature_skips_a_signature_inspect_cannot_build(mocker: MockerFixture) -> None:
    # older CPython raises building some C text signatures; the guard skips them rather than erroring the run
    mocker.patch("inspect.signature", autospec=True, side_effect=ValueError)
    assert _runtime_signature(html.parse) is None


@pytest.mark.skipif(
    sys.implementation.name == "pypy",
    reason="how much of __text_signature__ cpyext exposes varies by PyPy release: 7.3.23 answers for a heap type's "
    "methods where 7.3.19 answers only for module functions, so _COMPARABLE holds 120 entries on one and 8 on the "
    "other. The stub parser this guards is exercised on the CPython matrix",
)
def test_the_parity_check_covers_the_public_surface() -> None:
    # a stub-parsing regression that silently dropped entries would drop these simple, every-version signatures too
    assert {"parse", "escape", "Minify", "Document.opengraph"} <= set(_COMPARABLE)
    assert len(_COMPARABLE) > 40
