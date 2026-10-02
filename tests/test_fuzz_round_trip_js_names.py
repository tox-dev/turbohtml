"""The JS identifier and string-set oracle over a stubbed acorn reader, and the reader's Node protocol."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fuzz.round_trip_oracles import JsNames, OutOfScopeError, js_names_check

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pytest_mock import MockerFixture

    from turbohtml.clean import JSMinify

_Names = tuple[frozenset[str], frozenset[str]]
_INPUT: _Names = (frozenset({"x", "f"}), frozenset({"s"}))


@pytest.fixture
def reader(mocker: MockerFixture) -> Callable[[Callable[[str], _Names | None]], JsNames]:
    """Build a reader that answers through ``read``, standing in for the acorn process."""

    def build(read: Callable[[str], _Names | None]) -> JsNames:
        names = mocker.create_autospec(JsNames, instance=True)
        # PyPy's create_autospec drops a "read.side_effect" keyword, so the method gets it after creation
        names.read.side_effect = read
        return names

    return build


def _source_or(output: _Names | None) -> Callable[[str], _Names | None]:
    return lambda source: _INPUT if source == "source" else output


def _fold_only(text: str) -> Callable[[str, JSMinify], str]:
    return lambda _source, options: text if options.fold else "plain"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        pytest.param(_INPUT, None, id="same-sets"),
        pytest.param(
            (frozenset({"x", "f", "zz"}), frozenset({"s"})),
            "mangle=False fold=False: free identifier invented",
            id="identifier-invented",
        ),
        pytest.param(
            (frozenset({"x"}), frozenset({"s"})), "mangle=False fold=False: free identifier lost", id="identifier-lost"
        ),
        pytest.param(
            (frozenset({"x", "f"}), frozenset({"s", "t"})),
            "mangle=False fold=False: static string invented",
            id="string-invented",
        ),
        pytest.param(
            (frozenset({"x", "f"}), frozenset()), "mangle=False fold=False: static string lost", id="string-lost"
        ),
        pytest.param(None, "mangle=False fold=False: output is not JavaScript", id="output-not-javascript"),
    ],
)
def test_js_names_check_compares_the_unmangled_unfolded_output(
    reader: Callable[[Callable[[str], _Names | None]], JsNames], output: _Names | None, expected: str | None
) -> None:
    assert js_names_check("source", reader(_source_or(output)), lambda _source, _options: "out") == expected


def test_js_names_check_lets_fold_drop_but_not_invent(
    reader: Callable[[Callable[[str], _Names | None]], JsNames],
) -> None:
    sets = {
        "source": _INPUT,
        "plain": _INPUT,
        "dropped": (frozenset({"x"}), frozenset()),
        "invented": (frozenset({"x", "f", "zz"}), frozenset({"s"})),
    }
    names = reader(sets.__getitem__)
    verdicts = [js_names_check("source", names, _fold_only(output)) for output in ("dropped", "invented")]
    assert verdicts == [None, "mangle=False fold=True: free identifier invented"]


def test_js_names_check_skips_a_source_acorn_rejects(
    reader: Callable[[Callable[[str], _Names | None]], JsNames],
) -> None:
    with pytest.raises(OutOfScopeError):
        js_names_check("source", reader(lambda _source: None), lambda _source, _options: "out")


def test_js_names_check_skips_a_source_every_option_set_rejects(
    reader: Callable[[Callable[[str], _Names | None]], JsNames],
) -> None:
    def reject(_source: str, _options: JSMinify) -> str:
        msg = "unexpected token"
        raise ValueError(msg)

    with pytest.raises(OutOfScopeError):
        js_names_check("source", reader(_source_or(_INPUT)), reject)


def test_js_names_reader_requires_node(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch("fuzz.round_trip_oracles.shutil.which", return_value=None)
    with pytest.raises(FileNotFoundError, match="are required by the js-names oracle"):
        JsNames(tmp_path).read("x")


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        pytest.param({"free": ["a"], "strings": ["b"]}, (frozenset({"a"}), frozenset({"b"})), id="names"),
        pytest.param(None, None, id="rejected"),
    ],
)
def test_js_names_reader_speaks_json_lines(
    mocker: MockerFixture, tmp_path: Path, answer: dict[str, list[str]] | None, expected: _Names | None
) -> None:
    (tmp_path / "node_modules" / "eslint-scope").mkdir(parents=True)
    mocker.patch("fuzz.round_trip_oracles.shutil.which", return_value="node")
    process = mocker.MagicMock(**{"stdout.readline.return_value": json.dumps(answer) + "\n"})
    mocker.patch("fuzz.round_trip_oracles.subprocess.Popen", return_value=process)
    result = JsNames(tmp_path).read("a.b\n")
    assert (result, process.stdin.write.call_args.args) == (expected, ('"a.b\\n"\n',))


def test_js_names_reader_skips_a_tree_eslint_scope_cannot_scope(mocker: MockerFixture, tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "eslint-scope").mkdir(parents=True)
    mocker.patch("fuzz.round_trip_oracles.shutil.which", return_value="node")
    process = mocker.MagicMock(**{"stdout.readline.return_value": '{"error": "TypeError"}\n'})
    mocker.patch("fuzz.round_trip_oracles.subprocess.Popen", return_value=process)
    with pytest.raises(OutOfScopeError):
        JsNames(tmp_path).read("x")
