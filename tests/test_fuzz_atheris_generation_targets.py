from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.atheris_corpora import CorpusProfile, write_corpora
from fuzz.atheris_corpora import main as corpus_main
from fuzz.atheris_generation_targets import encoding_observation, generation_targets
from fuzz.atheris_targets import main, owner_inventory, public_targets
from fuzz.encoding_structure_generators import encoding_grammar
from fuzz.markdown_structure_generators import MarkdownProfileError, markdown_grammar
from fuzz.structure_generators import GenerationBudget, Grammar, generate

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("name", "data"),
    [
        pytest.param("markdown-source", b"**kept**\n", id="markdown"),
        pytest.param("markdown-html", b"<p><strong>kept</strong></p>", id="html"),
        pytest.param("encoding-bytes", b"<p>kept</p><!--comment-->tail", id="encoding"),
    ],
)
def test_generation_public_consumers_accept_domain_bytes(name: str, data: bytes) -> None:
    target: Final = next(target for target in public_targets() if target.name == name)
    target.callback(data)
    assert (target.name, target.exports) == (name, ())


@pytest.mark.parametrize("name", ["markdown-source", "markdown-html"])
def test_generation_public_consumers_reject_non_utf8(name: str) -> None:
    target: Final = next(target for target in public_targets() if target.name == name)
    with pytest.raises(UnicodeDecodeError):
        target.callback(b"\xff")


@pytest.mark.parametrize(
    ("name", "source"),
    [
        pytest.param("markdown-source", b"<video>unsupported</video>\n", id="markdown"),
        pytest.param("markdown-html", b"<video>unsupported</video>", id="html"),
    ],
)
def test_generation_public_consumers_reject_undeclared_profile(name: str, source: bytes) -> None:
    target: Final = next(target for target in public_targets() if target.name == name)
    with pytest.raises(MarkdownProfileError, match="no declared Markdown meaning"):
        target.callback(source)


@pytest.mark.parametrize(
    ("data", "encoding", "sniff", "expected"),
    [
        pytest.param(b"<p>\xe9</p>", "windows-1252", False, "é", id="fixed"),
        pytest.param(b"\xef\xbb\xbf<p>\xc3\xa9</p>", "windows-1252", False, "é", id="utf8-bom-overrides-fixed"),
        pytest.param(
            b"\xff\xfe" + "<p>é</p>".encode("utf-16-le"), "UTF-16BE", False, "é", id="utf16-bom-overrides-fixed"
        ),
        pytest.param(b"\xff\xfe" + "<p>é</p>".encode("utf-16-le"), "UTF-8", True, "é", id="utf16-bom"),
        pytest.param(b"<meta charset=windows-1252><p>\xe9</p>", "UTF-8", True, "é", id="prescan"),
        pytest.param(b" " * 1025 + b"<meta charset=windows-1251><p>\xc0</p>", "UTF-8", True, "\u0410", id="late-meta"),
    ],
)
def test_encoding_mutation_context(data: bytes, encoding: str, expected: str, *, sniff: bool) -> None:
    assert encoding_observation(data, encoding, sniff=sniff) == expected


def test_generation_unknown_execution_codec() -> None:
    with pytest.raises(LookupError):
        generation_targets("missing")


@pytest.mark.parametrize(
    ("profile", "grammar", "production", "context"),
    [
        pytest.param("markdown-source", markdown_grammar(), "markdown:reference", (None, False), id="markdown"),
        pytest.param("markdown-html", markdown_grammar(html=True), "markdown-html:thematic", (None, False), id="html"),
        pytest.param(
            "encoding", encoding_grammar(), "encoding:codec:windows-1252", ("windows-1252", False), id="fixed"
        ),
        pytest.param("encoding", encoding_grammar(), "encoding:bom:utf-16le", ("UTF-16LE", True), id="bom"),
    ],
)
def test_generation_corpus_preserves_context(
    tmp_path: Path, profile: CorpusProfile, grammar: Grammar, production: str, context: tuple[str | None, bool]
) -> None:
    case: Final = generate(grammar, random.SystemRandom(), GenerationBudget(64, 256), force=production)
    manifest: Final = write_corpora((case, case), grammar, tmp_path, profile)
    entry: Final = manifest.entries[0]
    assert (
        (tmp_path / entry.file).read_bytes(),
        entry.encoding,
        entry.sniff,
        entry.sha256,
        len(tuple(tmp_path.glob("**/" + entry.sha256))),
        manifest.entries[0] == manifest.entries[1],
        manifest.rejected,
    ) == (case.data, *context, hashlib.sha256(case.data).hexdigest(), 1, True, ())


def test_generation_corpus_rejects_changed_encoding_literal(tmp_path: Path) -> None:
    grammar: Final = encoding_grammar()
    case: Final = generate(grammar, random.SystemRandom(), GenerationBudget(64, 256), force="encoding:codec:utf-8")
    with pytest.raises(AssertionError, match="production literals"):
        write_corpora((replace(case, data=b"<p>wrong</p>"),), grammar, tmp_path, "encoding")


@pytest.mark.parametrize("profile", ["markdown-source", "markdown-html", "encoding"])
def test_generation_cli_sweeps_domain_productions(tmp_path: Path, profile: str) -> None:
    assert (
        corpus_main(("--output", str(tmp_path), "--profile", profile, "--count", "0", "--sweep", "--budget", "64")) == 0
    )
    manifest: Final = json.loads((tmp_path / "manifest.json").read_text())
    assert (len(manifest["entries"]), manifest["rejected"]) == (
        len(
            (
                encoding_grammar() if profile == "encoding" else markdown_grammar(html=profile == "markdown-html")
            ).productions
        ),
        [],
    )


def test_generation_cli_execution_context(tmp_path: Path, mocker: MockerFixture) -> None:
    run: Final = mocker.patch("fuzz.atheris_targets.fuzz", autospec=True)
    corpus: Final = tmp_path / "encoding"
    assert (
        main((
            "--target",
            "encoding-bytes",
            "--corpus",
            str(corpus),
            "--encoding",
            "windows-1252",
            "--sniff",
            "-atheris_runs=8",
        ))
        == 0
    )
    assert (json.loads(corpus.with_suffix(".json").read_text()), run.call_args.args[2]) == (
        {"target": "encoding-bytes", "exports": [], "corpus": str(corpus), "encoding": "windows-1252", "sniff": True},
        "encoding-bytes",
    )


def test_generation_preserves_qualified_owners() -> None:
    assert (len(owner_inventory()), len(public_targets())) == (209, 33)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        pytest.param("markdown-source", b"**kept**", id="source"),
        pytest.param("markdown-html", b"<p><strong>kept</strong></p>", id="html"),
    ],
)
def test_generation_first_conversion_loss(name: str, data: bytes) -> None:
    target: Final = next(
        target
        for target in generation_targets(render=lambda node: node.to_markdown().replace("kept", "changed"))
        if target.name == name
    )
    with pytest.raises(AssertionError, match="changes meaning"):
        target.callback(data)


def test_generation_changed_decoder_text() -> None:
    with pytest.raises(AssertionError, match="Encoded parser text differs"):
        encoding_observation(b"<p>kept</p>", decode=lambda _data, _codec: "<p>wrong</p>")


@pytest.mark.parametrize(
    ("profile", "target"),
    [pytest.param("xml", "xml-schema", id="xml"), pytest.param("css-stylesheet", "css-stylesheet", id="css")],
)
def test_generation_cli_existing_domain_consumers(tmp_path: Path, profile: str, target: str) -> None:
    assert corpus_main(("--output", str(tmp_path), "--profile", profile, "--count", "1", "--budget", "512")) == 0
    manifest: Final = json.loads((tmp_path / "manifest.json").read_text())
    assert ({entry["target"] for entry in manifest["entries"]}, manifest["rejected"]) == ({target}, [])
