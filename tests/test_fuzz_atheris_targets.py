from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.atheris_registry import validate_owners
from fuzz.atheris_targets import MODULES, main, owner_inventory, public_targets

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


def test_atheris_targets_complete_qualified_inventory() -> None:
    owners: Final = owner_inventory()
    assert (
        len(owners),
        len(MODULES),
        {name: owners[name] for name in ("turbohtml.parse", "turbohtml.parse_xml", "turbohtml.__main__.main")},
    ) == (
        209,
        20,
        {
            "turbohtml.parse": "html-document",
            "turbohtml.parse_xml": "xml-schema",
            "turbohtml.__main__.main": "html-cli",
        },
    )


def test_atheris_targets_missing_group_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing="):
        validate_owners(tuple(target for target in public_targets() if target.name != "javascript"), MODULES)


def test_atheris_targets_duplicate_group_is_rejected() -> None:
    targets: Final = public_targets()
    with pytest.raises(ValueError, match="Duplicate export owners"):
        validate_owners((*targets, targets[0]), MODULES)


def test_atheris_targets_cli_manifest_and_flags(tmp_path: Path, mocker: MockerFixture) -> None:
    run: Final = mocker.patch("fuzz.atheris_targets.fuzz", autospec=True)
    corpus: Final = tmp_path / "html-tokenizer"
    assert main(("--target", "html-tokenizer", "--corpus", str(corpus), "-atheris_runs=64", "-max_len=128")) == 0
    assert (json.loads(corpus.with_suffix(".json").read_text()), run.call_args.args[2:]) == (
        {
            "target": "html-tokenizer",
            "exports": ["turbohtml.Token", "turbohtml.TokenType", "turbohtml.Tokenizer", "turbohtml.tokenize"],
            "corpus": str(corpus),
        },
        ("html-tokenizer", (run.call_args.args[3][0], str(corpus), "-atheris_runs=64", "-max_len=128")),
    )
