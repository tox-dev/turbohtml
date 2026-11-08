#!/usr/bin/env python3
"""
Derive turbohtml's version from git tags for the meson-python build.

meson calls this at configure time and uses the printed string as the project
version, the way setuptools-scm / hatch-vcs derive a version from the latest
git tag. During ``meson dist`` it is called with ``--write``/``--meson-dist`` to
freeze the resolved version into the sdist, so a wheel built from that sdist (no
``.git`` present) still reports the right version.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path
from typing import Final

# tools/ lives next to src/, so the project root is one level up from this file.
_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_VERSION_FILE: Final[Path] = Path("src/turbohtml/_version.py")
# Used only before the first release tag exists, so dev builds still get a PEP 440 version.
_FALLBACK: Final[str] = "0.0.0"


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=_ROOT,
            check=False,
        )
    except FileNotFoundError:  # git not installed, e.g. building from an unpacked sdist
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _version_from_git() -> str | None:
    if not (_ROOT / ".git").exists():
        return None
    if (described := _git("describe", "--tags", "--long", "--match", "[0-9]*")) is not None:
        tag, distance, commit = described.rsplit("-", 2)
        if int(distance) == 0:
            return tag
        return f"{tag}.dev{distance}+{commit}"  # commit already carries the leading "g"
    if (commit := _git("rev-parse", "--short", "HEAD")) is None:
        return None  # not a git checkout at all
    return f"{_FALLBACK}.dev{(_git('rev-list', '--count', 'HEAD') or '0')}+g{commit}"


def _version_from_file() -> str | None:
    path = _ROOT / _VERSION_FILE
    if not path.is_file():
        return None
    match = re.search(
        r"(?#the __version__ assignment, optionally annotated Final[str], with a double-quoted value)"
        r'__version__(?:: Final\[str\])? = "(?P<version>[^"]+)"',
        path.read_text(encoding="utf-8"),
    )
    return match.group("version") if match else None


def resolve() -> str:
    # Return the version from git, then a frozen sdist file, then the fallback.
    return _version_from_git() or _version_from_file() or _FALLBACK


def main() -> None:
    # Print the resolved version, or freeze it into a file for the sdist.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", type=Path, help="write __version__ to this file instead of printing it")
    parser.add_argument("--meson-dist", action="store_true", help="resolve --write against MESON_DIST_ROOT")
    parser.add_argument("--version", help="use the configured Meson project version")
    args = parser.parse_args()

    version: Final = resolve() if args.version is None else args.version
    if args.write is None:
        print(version)
        return
    target = Path(os.environ.get("MESON_DIST_ROOT", "")) / args.write if args.meson_dist else args.write
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from pathlib import Path as _Path\n"
        "from typing import Final\n\n"
        "from . import __file__ as _PACKAGE_FILE\n\n"
        "__version__: Final[str]\n"
        "if _Path(__file__).parent == _Path(_PACKAGE_FILE).parent:\n"
        f'    __version__ = "{version}"\n'
        "else:\n"
        "    # Editable rebuilds can change Meson's version without replacing installed metadata.\n"
        "    from importlib.metadata import version as _installed_version\n\n"
        '    __version__ = _installed_version("turbohtml")\n\n'
        '__all__ = ["__version__"]\n',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
