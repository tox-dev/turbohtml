"""End-to-end tests for the ``python -m turbohtml`` command-line interface."""

from __future__ import annotations

import io
import subprocess  # noqa: S404  # the entry-point test runs turbohtml in a clean interpreter; the command is fixed, not input
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from turbohtml.__main__ import main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("argv", "stdin", "expected"),
    [
        pytest.param(["minify"], "<p>a  b</p><!-- c -->", "<p>a b", id="minify"),
        pytest.param(
            ["minify", "--minify-css"],
            "<style>a { color : red }</style>",
            "<style>a{color:red}</style>",
            id="minify-css-flag",
        ),
        pytest.param(["minify-css"], "a { color: #ffffff }", "a{color:#fff}", id="minify-css"),
        pytest.param(["minify-js"], "const  x = 1 ;", "const x=1", id="minify-js"),
        pytest.param(["to-markdown"], "<h1>T</h1><p>b</p>", "# T\n\nb", id="to-markdown"),
        pytest.param(["to-text"], "<h1>T</h1><p>b</p>", "T\n\nb", id="to-text"),
        pytest.param(
            ["sanitize"], "<b>hi</b><script>x()</script>", "<b>hi</b>&lt;script&gt;x()&lt;/script&gt;", id="sanitize"
        ),
        pytest.param(["detect"], "café èè", "UTF-8\n", id="detect"),
    ],
)
def test_cli_subcommand_stdin_to_stdout(
    argv: list[str], stdin: str, expected: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(read=io.StringIO(stdin).read, buffer=io.BytesIO(stdin.encode())))
    assert main(argv) == 0
    assert capsys.readouterr().out == expected


def test_cli_reads_file_argument(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "in.html"
    source.write_text("<p>a  b</p>", encoding="utf-8")
    assert main(["minify", str(source)]) == 0
    assert capsys.readouterr().out == "<p>a b"


def test_cli_dash_reads_stdin(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("<p>a  b</p>"))
    assert main(["minify", "-"]) == 0
    assert capsys.readouterr().out == "<p>a b"


def test_cli_detect_reads_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "in.txt"
    source.write_bytes("café èè".encode())
    assert main(["detect", str(source)]) == 0
    assert capsys.readouterr().out == "UTF-8\n"


def test_cli_writes_output_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("<p>a  b</p>"))
    destination = tmp_path / "out.html"
    assert main(["minify", "-o", str(destination)]) == 0
    assert destination.read_text(encoding="utf-8") == "<p>a b"


def test_cli_detect_empty_input_fails(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"")))
    assert main(["detect"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == "turbohtml detect: no encoding detected\n"


def test_cli_library_error_maps_to_exit_code(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("const ="))
    assert main(["minify-js"]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err.startswith("turbohtml minify-js: ")


def test_cli_missing_file_maps_to_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["minify", "does-not-exist.html"]) == 1
    assert capsys.readouterr().err.startswith("turbohtml minify: ")


def test_cli_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_cli_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "turbohtml", "minify-css"],
        input="a { color: #ffffff }",
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "a{color:#fff}"
