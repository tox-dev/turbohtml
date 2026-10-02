"""The super-linear lane's inputs: the committed regression list and the literals harvested from the C sources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest
from fuzz.amplify import TARGETS, Regression, Shape, alphabet, load_regressions

_COMMITTED: Final = Path(__file__).parents[1] / "tools" / "fuzz" / "amplify_regressions.json"


def _write(tmp_path: Path, rows: object) -> Path:
    (path := tmp_path / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_committed_regressions_name_each_target_once() -> None:
    rows = load_regressions(_COMMITTED)
    assert len({(row.name, row.target) for row in rows}) == len(rows)


def test_load_regressions_expands_one_row_per_target(tmp_path: Path) -> None:
    shape = {"kind": "repeat", "left": "a<?"}
    path = _write(tmp_path, [{"name": "pi", "source": "s", "targets": ["parse", "tokenize"], "shape": shape}])
    expected = [Regression("pi", target, Shape("repeat", "a<?"), "s", 1024) for target in ("parse", "tokenize")]
    assert load_regressions(path) == expected


def test_load_regressions_keeps_a_row_base(tmp_path: Path) -> None:
    row = {"name": "big", "source": "s", "targets": ["idna"], "base": 65536, "shape": {"kind": "repeat", "left": "a"}}
    assert [entry.base for entry in load_regressions(_write(tmp_path, [row]))] == [65536]


def test_load_regressions_rejects_an_unknown_target(tmp_path: Path) -> None:
    row = {"name": "typo", "source": "s", "targets": ["parse", "prase"], "shape": {"kind": "repeat", "left": "a"}}
    with pytest.raises(ValueError, match="typo: unknown target prase"):
        load_regressions(_write(tmp_path, [row]))


def test_load_regressions_rejects_an_invalid_shape(tmp_path: Path) -> None:
    row = {"name": "empty", "source": "s", "targets": ["parse"], "shape": {"kind": "repeat", "left": ""}}
    with pytest.raises(ValueError, match="non-empty left piece"):
        load_regressions(_write(tmp_path, [row]))


@pytest.mark.parametrize(
    ("sources", "literals"),
    [
        # match_kw("--"), match_kw("doctype"), match_kw("[CDATA[") and ch == '<' in the markup-declaration states
        pytest.param(("tokenizer/statemachine_run.h",), {"--", "doctype", "[CDATA[", "<", "&"}, id="comparisons"),
        pytest.param(("data/tag_atom.h",), {"script", "<script>", "</script>"}, id="tag-table-as-tags"),
        pytest.param(("clean/sanitize.c",), {chr(0x200B), chr(0xFEFF)}, id="code-point-case-labels"),
    ],
)
def test_alphabet_harvests(sources: tuple[str, ...], literals: set[str]) -> None:
    assert literals <= set(alphabet(sources))


def test_alphabet_skips_literals_in_comments() -> None:
    # idna.c names the "xn--" prefix only in comments; its code compares that prefix one character at a time
    letters = alphabet(("url/idna.c",))
    assert ("x" in letters, "xn--" in letters) == (True, False)


@pytest.mark.parametrize("name", sorted(TARGETS))
def test_alphabet_of_every_target_is_sorted_and_short(name: str) -> None:
    letters = alphabet(TARGETS[name].sources)
    assert (letters == tuple(sorted(set(letters))), max(map(len, letters)) <= 32) == (True, True)
