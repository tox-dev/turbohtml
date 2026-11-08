from __future__ import annotations

import json
import secrets
from typing import Final

import pytest
from fuzz.resource_canary import ESCAPES, Attempt, compare, generate, main


@pytest.mark.parametrize("escape", sorted(ESCAPES))
def test_resource_canary_escape_stays_contained(escape: str) -> None:
    token: Final = f"canary{secrets.token_hex(8)}"
    attempt: Final = Attempt(seed=0, escape=escape, token=token)
    assert [(row.engine, row.leaked) for row in compare(attempt)] == [
        ("xslt", False),
        ("relaxng", False),
    ]


@pytest.mark.parametrize("escape", ["parent", "symlink"])
def test_resource_canary_inside_root_is_observed(escape: str) -> None:
    token: Final = f"canary{secrets.token_hex(8)}"
    attempt: Final = Attempt(seed=0, escape=escape, token=token)
    assert [(row.engine, row.leaked) for row in compare(attempt, negative_control=True)] == [
        ("xslt", True),
        ("relaxng", True),
    ]


def test_resource_canary_generate_is_seeded() -> None:
    assert [generate(seed) == generate(5) for seed in (5, 6)] == [True, False]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        pytest.param(["--cases", "3"], (0, {"cases": 3, "findings": 0}), id="contained"),
        pytest.param(["--cases", "1", "--negative-control"], (1, {"cases": 1, "findings": 2}), id="negative-control"),
    ],
)
def test_resource_canary_cli(
    capsys: pytest.CaptureFixture[str], arguments: list[str], expected: tuple[int, dict[str, int]]
) -> None:
    status: Final = main(arguments)
    assert (status, json.loads(capsys.readouterr().out.splitlines()[-1])["summary"]) == expected


def test_resource_canary_cli_rejects_case_count(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--cases", "0"])
    assert "--cases must be between 1 and 256" in capsys.readouterr().err
