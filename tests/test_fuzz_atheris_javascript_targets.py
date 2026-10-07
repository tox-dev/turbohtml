from __future__ import annotations

import json
import os
import random
import shutil
import sys
from pathlib import Path
from subprocess import run  # ruff: ignore[suspicious-subprocess-import] - startup resolution needs a fresh interpreter.
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.atheris_corpora import write_corpora
from fuzz.atheris_javascript_targets import javascript_observation, javascript_targets
from fuzz.atheris_targets import main, owner_inventory
from fuzz.round_trip_oracles import OutOfScopeError
from fuzz.structure_generators import Production, compile_grammar, generate

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from turbohtml.clean import JSMinify

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None
    or not (Path(__file__).parents[1] / "tools/bench/node/node_modules/eslint-scope").is_dir(),
    reason="Node and the pinned JavaScript reader packages are required",
)


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(b"", "", id="empty"),
        pytest.param(b'consume ( "kept" , external );', 'consume("kept",external)', id="free-names"),
        pytest.param('consume("水😀");'.encode(), 'consume("水😀")', id="unicode"),
        pytest.param(b"let value = 1 + 2; consume(value);", "let value=1+2;consume(value)", id="unfolded"),
    ],
)
def test_javascript_exact_source(source: bytes, expected: str) -> None:
    assert javascript_observation(source) == expected


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("source", "error"),
    [
        pytest.param(b"\xff", UnicodeDecodeError, id="invalid-utf8"),
        pytest.param(b"(", OutOfScopeError, id="invalid-javascript"),
    ],
)
def test_javascript_callback_rejects_input(source: bytes, error: type[Exception]) -> None:
    with pytest.raises(error):
        javascript_targets()[0].callback(source)


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("source", "error", "message"),
    [
        pytest.param(b"(", OutOfScopeError, None, id="invalid-input-first"),
        pytest.param(b'consume("kept",external);', AssertionError, "not a fixpoint", id="valid-input-nonfixpoint"),
    ],
)
def test_javascript_rejects_independent_invalid_input_first(
    source: bytes, error: type[Exception], message: str | None
) -> None:
    def unstable(source: str, _options: JSMinify) -> str:
        return source + ";"

    with pytest.raises(error, match=message):
        javascript_observation(source, unstable)


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("output", "message"),
    [
        pytest.param('consume("lost",external);', "static string", id="wrong-string"),
        pytest.param('invented("kept",external);', "free identifier", id="wrong-name"),
        pytest.param("(", "not JavaScript", id="invalid-output"),
    ],
)
def test_javascript_rejects_wrong_stable_output(output: str, message: str) -> None:
    def wrong(_source: str, _options: JSMinify) -> str:
        return output

    with pytest.raises(AssertionError, match=message):
        javascript_observation(b'consume("kept",external);', wrong)


@pytest.mark.oracle
def test_javascript_owns_public_minifier() -> None:
    assert (len(owner_inventory()), owner_inventory()["turbohtml.clean.minify_js"]) == (209, "javascript")


@pytest.mark.oracle
def test_javascript_cli_initializes_reader_and_routes_corpus(tmp_path: Path, mocker: MockerFixture) -> None:
    run: Final = mocker.patch("fuzz.atheris_targets.fuzz", autospec=True)
    corpus: Final = tmp_path / "javascript"
    assert main(("--target", "javascript", "--corpus", str(corpus), "-atheris_runs=8")) == 0
    assert (json.loads(corpus.with_suffix(".json").read_text()), run.call_args.args[2]) == (
        {"target": "javascript", "exports": ["turbohtml.clean.minify_js"], "corpus": str(corpus)},
        "javascript",
    )


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("source", "error"),
    [
        pytest.param(b'consume("kept",external);', None, id="valid"),
        pytest.param(b"(", "OutOfScopeError", id="syntax"),
        pytest.param(b"\xff", "UnicodeDecodeError", id="utf8"),
    ],
)
def test_javascript_corpus_admission(tmp_path: Path, source: bytes, error: str | None) -> None:
    grammar: Final = compile_grammar((Production("script", "root", (source,), 1, "fixture:1"),), "root")
    manifest: Final = write_corpora((generate(grammar, random.SystemRandom()),), grammar, tmp_path, "javascript")
    if error is not None:
        assert (manifest.entries, tuple(entry.exception for entry in manifest.rejected)) == ((), (error,))
    else:
        assert (
            tuple(entry.target for entry in manifest.entries),
            tuple((tmp_path / entry.file).read_bytes() for entry in manifest.entries),
            tuple(entry.productions for entry in manifest.entries),
            manifest.rejected,
        ) == (("javascript",), (source,), ((("script", "fixture:1"),),), ())


@pytest.mark.oracle
def test_javascript_cli_dependency_failure_precedes_corpus_creation(tmp_path: Path) -> None:
    corpus: Final = tmp_path / "javascript"
    result: Final = run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed interpreter and owned CLI.
        [sys.executable, "-m", "fuzz.atheris_targets", "--target", "javascript", "--corpus", str(corpus)],
        env={
            **os.environ,
            "PATH": str(tmp_path),
            "PYTHONPATH": str(Path(__file__).parents[1] / "tools"),
            "GCOV_PREFIX": str(tmp_path / "gcda"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode, "required by the js-names oracle" in result.stderr, corpus.exists()) == (1, True, False)
