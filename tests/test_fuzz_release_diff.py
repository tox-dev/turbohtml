"""The previous-release differential: its error-normalization knob, its workers, and a HEAD-against-HEAD run."""

from __future__ import annotations

import sys
import time
import venv
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fuzz.release_diff import Worker, compare, main

from turbohtml.clean import minify_js

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytest_mock import MockerFixture

_TYPE_ERROR: dict[str, object] = {"error": ["ValueError", "a"]}


@pytest.mark.parametrize(
    ("head", "release", "errors", "expected"),
    [
        pytest.param({"ok": "x"}, {"ok": "x"}, "message", None, id="same-output"),
        pytest.param({"ok": "x"}, {"ok": "y"}, "any", "output differs", id="output-differs"),
        pytest.param(_TYPE_ERROR, {"error": ["ValueError", "b"]}, "message", "error differs", id="message-strict"),
        pytest.param(_TYPE_ERROR, {"error": ["ValueError", "b"]}, "type", None, id="message-ignored"),
        pytest.param(_TYPE_ERROR, {"error": ["TypeError", "a"]}, "type", "error differs", id="type-strict"),
        pytest.param(_TYPE_ERROR, {"error": ["TypeError", "a"]}, "any", None, id="any-error"),
        pytest.param(_TYPE_ERROR, {"ok": "x"}, "any", "HEAD raises", id="head-raises"),
        pytest.param({"ok": "x"}, _TYPE_ERROR, "any", "release raises", id="release-raises"),
        pytest.param({"crashed": 1}, {"ok": "x"}, "any", "HEAD crashed", id="head-crashed"),
        pytest.param({"ok": "x"}, {"crashed": -11}, "any", "release crashed", id="release-crashed"),
    ],
)
def test_release_diff_compare(
    head: dict[str, object], release: dict[str, object], errors: str, expected: str | None
) -> None:
    assert compare(head, release, errors) == expected


@pytest.fixture(scope="module")
def head() -> Iterator[Worker]:
    with Worker(Path(sys.executable)) as worker:
        yield worker


def test_release_worker_answers_an_operation(head: Worker) -> None:
    assert head.answer("minify_css", "a{color:#ff0000}") == {"ok": "a{color:red}"}


def test_release_worker_reports_an_error(head: Worker) -> None:
    with pytest.raises(ValueError, match="unexpected token") as caught:
        minify_js("(")
    assert head.answer("minify_js", "(") == {"error": ["ValueError", str(caught.value)]}


def test_release_worker_perturbs_one_operation() -> None:
    with Worker(Path(sys.executable), perturb="minify_css") as worker:
        assert worker.answer("minify_css", "a{}") == {"ok": "\0perturbed"}


def test_release_worker_restarts_after_dying() -> None:
    with Worker(Path(sys.executable)) as worker:
        assert (worker.answer("no-such-operation", ""), worker.answer("minify_css", "a{}")) == (
            {"crashed": 1},
            {"ok": ""},
        )


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in interpreter is a POSIX shell script")
def test_release_worker_reports_a_worker_that_exited_before_reading(
    tmp_path: Path,
) -> None:  # pragma: win32 no cover
    (python := tmp_path / "python").write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    python.chmod(0o755)
    with Worker(python) as worker:
        time.sleep(1)  # the stand-in has exited before the first case is written, so the write meets a closed pipe
        assert worker.answer("minify_css", "a{}") == {"crashed": 3}


def test_release_diff_cli_agrees_with_itself(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--minutes", "0", "--release-python", sys.executable, "--crash-dir", str(tmp_path)])
    assert (code, capsys.readouterr().out.splitlines()[-1], sorted(tmp_path.iterdir())) == (
        0,
        f"0 difference(s) written to {tmp_path}",
        [],
    )


def test_release_diff_cli_fails_without_a_release(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main([
        "--minutes",
        "0",
        "--release-python",
        str(_python_without_turbohtml(tmp_path)),
        "--crash-dir",
        str(tmp_path / "crashes"),
    ])
    assert (code, capsys.readouterr().err) == (2, "VACUOUS RUN: the release answered too few cases\n")


def test_release_diff_cli_fails_a_blind_control(
    mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch("fuzz.release_diff.compare", return_value=None)
    code = main(["--minutes", "0", "--release-python", sys.executable, "--crash-dir", str(tmp_path)])
    assert (code, capsys.readouterr().err) == (2, "BLIND ORACLE: the perturbed worker went unreported\n")


def _python_without_turbohtml(tmp_path: Path) -> Path:
    if sys.platform == "win32":  # pragma: win32 cover
        venv.create(tmp_path / "empty", with_pip=False)
        python = tmp_path / "empty" / "Scripts" / "python"
    else:  # pragma: win32 no cover
        # on 3.10 a venv made from inside the tox venv cannot find its standard library, so its worker dies at startup;
        # -S keeps the stdlib and drops site-packages, which is where turbohtml lives
        (python := tmp_path / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n', encoding="utf-8")
        python.chmod(0o755)
    return python
