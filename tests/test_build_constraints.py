"""Build constraints follow the locked Meson and PGO tools."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING, Final, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

_SCRIPT: Final = Path(__file__).resolve().parents[1] / "tools" / "build_constraints.py"
_RENDER_CONSTRAINTS: Final = cast("Callable[[Path], str]", runpy.run_path(str(_SCRIPT))["render_constraints"])


def test_build_constraints_render_all_required_packages(tmp_path: Path) -> None:
    lock = tmp_path / "pylock.toml"
    lock.write_text(
        dedent(
            """\
            [[packages]]
            name = "tenacity"
            version = "9.2.1"
            [[packages]]
            name = "meson"
            version = "1.12.1"
            [[packages]]
            name = "packaging"
            version = "26.3"
            [[packages]]
            name = "httpx2"
            version = "2.16.0"
            [[packages]]
            name = "ninja"
            version = "1.13.2"
            [[packages]]
            name = "meson-python"
            version = "0.22.1"
            [[packages]]
            name = "pyproject-metadata"
            version = "0.12.1"
            [[packages]]
            name = "unrelated"
            version = "1.0"
            """,
        ),
        encoding="utf-8",
    )
    assert _RENDER_CONSTRAINTS(lock) == dedent(
        """\
        httpx2==2.16.0
        meson==1.12.1
        meson-python==0.22.1
        ninja==1.13.2
        packaging==26.3
        pyproject-metadata==0.12.1
        tenacity==9.2.1
        """,
    )


def test_build_constraints_reject_missing_package(tmp_path: Path) -> None:
    lock = tmp_path / "pylock.toml"
    lock.write_text('[[packages]]\nname = "meson"\nversion = "1.12.1"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing locked build packages: httpx2"):
        _RENDER_CONSTRAINTS(lock)


def test_build_constraints_reject_conflicting_versions(tmp_path: Path) -> None:
    lock = tmp_path / "pylock.toml"
    lock.write_text(
        '[[packages]]\nname = "meson"\nversion = "1.12.1"\n[[packages]]\nname = "meson"\nversion = "1.12.2"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="multiple locked versions of meson"):
        _RENDER_CONSTRAINTS(lock)


def test_build_constraints_cli_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "build-constraints.txt"
    monkeypatch.setattr(sys, "argv", ["build_constraints", "pylock.toml", str(output)])
    runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert "meson-python==" in output.read_text(encoding="utf-8")
