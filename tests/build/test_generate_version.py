from __future__ import annotations

import os
import shutil
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # exercise the generator's public CLI
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Final

import pytest

from turbohtml import _version


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3.dev4+gabc"], ids=["release", "development"])
def test_generate_version_configured_value(version_project: Path, version: str) -> None:
    assert _version_cli(version_project, "--version", version) == version + "\n"


@pytest.mark.parametrize(
    "declaration",
    ['__version__ = "1.2.3"', '__version__: Final[str] = "1.2.3"'],
    ids=["legacy", "annotated"],
)
def test_generate_version_reads_frozen_value(version_project: Path, declaration: str) -> None:
    target: Final = version_project / "src/turbohtml/_version.py"
    target.parent.mkdir(parents=True)
    target.write_text(declaration + "\n", encoding="utf-8")
    assert _version_cli(version_project) == "1.2.3\n"


def test_generate_version_written_value_round_trips(version_project: Path) -> None:
    _version_cli(version_project, "--write", "src/turbohtml/_version.py", "--version", "1.2.3.dev4+gabc")
    assert _version_cli(version_project) == "1.2.3.dev4+gabc\n"


@pytest.mark.parametrize(("editable", "expected"), [(False, "1.2.3"), (True, "9.8.7")], ids=["wheel", "editable"])
def test_generate_version_import_matches_layout(version_project: Path, *, editable: bool, expected: str) -> None:
    package: Final = version_project / "sample"
    package.mkdir()
    generated: Final = version_project / "generated" if editable else package
    _version_cli(version_project, "--write", str(generated / "_version.py"), "--version", "1.2.3")
    package.joinpath("__init__.py").write_text(
        f"__path__.append({str(generated)!r})\nfrom ._version import __version__\n", encoding="utf-8"
    )
    metadata: Final = version_project / "turbohtml-9.8.7.dist-info"
    metadata.mkdir()
    metadata.joinpath("METADATA").write_text(
        "Metadata-Version: 2.1\nName: turbohtml\nVersion: 9.8.7\n", encoding="utf-8"
    )
    result: Final = subprocess.run(  # fixed Python argv
        [sys.executable, "-c", "import sample; print(sample.__version__)"],
        cwd=version_project,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == expected + "\n"


def test_generate_version_installed_source_in_wheel_layout(version_project: Path) -> None:
    installed: Final = Path(_version.__file__)
    frozen: Final = version_project / "src/turbohtml/_version.py"
    frozen.parent.mkdir(parents=True)
    frozen.symlink_to(installed)
    package: Final = version_project / "turbohtml"
    package.mkdir()
    package.joinpath("_version.py").symlink_to(installed)
    package.joinpath("__init__.py").write_text("from ._version import __version__\n", encoding="utf-8")
    metadata: Final = version_project / "turbohtml-9.8.7.dist-info"
    metadata.mkdir()
    metadata.joinpath("METADATA").write_text(
        "Metadata-Version: 2.1\nName: turbohtml\nVersion: 9.8.7\n", encoding="utf-8"
    )
    spec: Final = spec_from_file_location("turbohtml.version_fixture", package / "__init__.py")
    assert spec is not None
    assert spec.loader is not None
    module: Final = module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(version_project))
    try:
        spec.loader.exec_module(module)
        assert module.__version__ + "\n" == _version_cli(version_project)
    finally:
        sys.path.remove(str(version_project))
        sys.modules.pop(f"{spec.name}._version", None)
        del sys.modules[spec.name]


def test_generate_version_sdist_keeps_configured_value(version_project: Path) -> None:
    _version_cli(version_project, "--meson-dist", "--write", "src/turbohtml/_version.py", "--version", "1.2.3")
    dist: Final = version_project / "sdist"
    dist.joinpath("tools").mkdir()
    shutil.copyfile(version_project / "tools/generate_version.py", dist / "tools/generate_version.py")
    assert _version_cli(dist) == "1.2.3\n"


def test_generate_version_sdist_ignores_enclosing_repository(version_repository: Path) -> None:
    _version_cli(version_repository, "--meson-dist", "--write", "src/turbohtml/_version.py", "--version", "1.2.3")
    dist: Final = version_repository / "sdist"
    dist.joinpath("tools").mkdir()
    shutil.copyfile(version_repository / "tools/generate_version.py", dist / "tools/generate_version.py")
    assert _version_cli(dist) == "1.2.3\n"


@pytest.mark.parametrize("worktree", [False, True], ids=["git-directory", "git-file"])
def test_generate_version_uses_own_repository(version_repository: Path, *, worktree: bool) -> None:
    project: Final = version_repository / "checkout" if worktree else version_repository
    if worktree:
        git: Final = shutil.which("git")
        assert git is not None
        subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # local fixture repository
            [git, "-C", str(version_repository), "worktree", "add", "--detach", str(project)],
            check=True,
            capture_output=True,
        )
        project.joinpath("tools").mkdir()
        shutil.copyfile(version_repository / "tools/generate_version.py", project / "tools/generate_version.py")
    _version_cli(project, "--write", "src/turbohtml/_version.py", "--version", "1.2.3")
    assert _version_cli(project) == "2.0.0\n"


@pytest.fixture
def version_project(tmp_path: Path) -> Path:
    tmp_path.joinpath("tools").mkdir()
    shutil.copyfile(Path(__file__).parents[2] / "tools/generate_version.py", tmp_path / "tools/generate_version.py")
    return tmp_path


@pytest.fixture
def version_repository(version_project: Path) -> Path:
    git: Final = shutil.which("git")
    assert git is not None
    for args in (
        ("init", "--quiet"),
        (
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "test: seed enclosing repository",
        ),
        ("tag", "2.0.0"),
    ):
        subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # local fixture repository
            [git, "-C", str(version_project), *args],
            check=True,
            capture_output=True,
        )
    return version_project


def _version_cli(project: Path, *args: str) -> str:
    return subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed Python executable and CLI
        [sys.executable, str(project / "tools/generate_version.py"), *args],
        cwd=project,
        env={
            **os.environ,
            "GIT_CEILING_DIRECTORIES": str(project.parent.parent),
            "MESON_DIST_ROOT": str(project / "sdist"),
        },
        capture_output=True,
        text=True,
        check=True,
    ).stdout
