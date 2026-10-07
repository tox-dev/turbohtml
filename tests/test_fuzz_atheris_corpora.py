from __future__ import annotations

import hashlib
import json
import random
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.atheris_corpora import main, write_corpora
from fuzz.html_structure_generators import html_grammar_complete
from fuzz.structure_generators import Identifier, Production, compile_grammar, generate

if TYPE_CHECKING:
    from pathlib import Path

    from fuzz.atheris_corpora import CorpusProfile


@pytest.mark.parametrize(
    ("profile", "production", "source", "targets"),
    [
        pytest.param(
            "html",
            "html:document:fixture",
            b"<html><body><p>x</p></body></html>",
            ("html-document", "html-incremental", "html-tokenizer"),
            id="document",
        ),
        pytest.param("html", "html:fragment:fixture", b"<p>x</p>", ("html-fragment", "html-tokenizer"), id="fragment"),
        pytest.param("xml", "xml:fixture", b"<root>text</root>", ("xml-schema",), id="xml"),
        pytest.param("css-stylesheet", "css:sheet", b"p { color: red; }", ("css-stylesheet",), id="stylesheet"),
        pytest.param("css-declaration", "css:declaration", b"color: red;", ("css-object-model",), id="declaration"),
        pytest.param("css-selector", "css:selector", b"p.x", ("css-translate",), id="selector"),
    ],
)
def test_corpus_routes_exact_bytes(
    tmp_path: Path, profile: CorpusProfile, production: str, source: bytes, targets: tuple[str, ...]
) -> None:
    grammar: Final = compile_grammar((Production(production, "root", (source,), 1, "fixture:1"),), "root")
    case: Final = generate(grammar, random.SystemRandom())
    manifest: Final = write_corpora((case,), grammar, tmp_path, profile)
    assert (
        tuple(entry.target for entry in manifest.entries),
        tuple((tmp_path / entry.file).read_bytes() for entry in manifest.entries),
        tuple(entry.productions for entry in manifest.entries),
        manifest.rejected,
    ) == (targets, (source,) * len(targets), (((production, "fixture:1"),),) * len(targets), ())
    assert (
        json.loads((tmp_path / "manifest.json").read_text())["entries"][0]["sha256"]
        == hashlib.sha256(source).hexdigest()
    )


def test_corpus_deduplicates_bytes_preserves_traces(tmp_path: Path) -> None:
    grammar: Final = compile_grammar(
        (
            Production("first", "root", (b"<p>x</p>",), 1, "fixture:1"),
            Production("second", "root", (b"<p>x</p>",), 1, "fixture:2"),
        ),
        "root",
    )
    cases: Final = tuple(generate(grammar, random.SystemRandom(), force=name) for name in ("first", "second"))
    manifest: Final = write_corpora(cases, grammar, tmp_path, "html")
    assert (
        len(tuple((tmp_path / "html-fragment").iterdir())),
        tuple(entry.productions for entry in manifest.entries if entry.target == "html-fragment"),
    ) == (1, ((("first", "fixture:1"),), (("second", "fixture:2"),)))


def test_corpus_rejects_invalid_input_before_writing(tmp_path: Path) -> None:
    grammar: Final = compile_grammar((Production("invalid", "root", (b"\xff",), 1, "fixture:1"),), "root")
    manifest: Final = write_corpora((generate(grammar, random.SystemRandom()),), grammar, tmp_path, "css-stylesheet")
    assert (
        manifest.entries,
        tuple(entry.exception for entry in manifest.rejected),
        (tmp_path / "css-stylesheet").exists(),
    ) == ((), ("UnicodeDecodeError",), False)


def test_corpus_cli_creates_target_files(tmp_path: Path) -> None:
    assert main(("--output", str(tmp_path), "--count", "1", "--seed", "1")) == 0
    manifest: Final = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["entries"]
    assert all((tmp_path / entry["file"]).is_file() for entry in manifest["entries"])


def test_corpus_cli_empty_count(tmp_path: Path) -> None:
    assert main(("--output", str(tmp_path), "--count", "0")) == 0
    assert json.loads((tmp_path / "manifest.json").read_text()) == {"entries": [], "rejected": []}


def test_corpus_cli_sweep_preserves_productions(tmp_path: Path) -> None:
    assert main(("--output", str(tmp_path), "--count", "0", "--sweep")) == 0
    manifest: Final = json.loads((tmp_path / "manifest.json").read_text())
    assert {production.name for production in html_grammar_complete().productions} <= {
        production[0] for entry in manifest["entries"] for production in entry["productions"]
    }


def test_corpus_preserves_generated_bindings(tmp_path: Path) -> None:
    grammar: Final = compile_grammar(
        (
            Production(
                "bound", "root", (b'<p id="', Identifier("element", definition=True), b'">text</p>'), 1, "fixture:1"
            ),
        ),
        "root",
    )
    case: Final = generate(grammar, random.SystemRandom())
    manifest: Final = write_corpora((case,), grammar, tmp_path, "html")
    assert tuple(entry.bindings for entry in manifest.entries) == (case.bindings, case.bindings)
    assert json.loads((tmp_path / "manifest.json").read_text())["entries"][0]["bindings"] == [["element", "x"]]
