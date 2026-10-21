"""Derive Meson and PGO build constraints from the full project lock."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path
from typing import Final

_BUILD_PACKAGES: Final = frozenset(
    {"httpx2", "meson", "meson-python", "ninja", "packaging", "patchelf", "pyproject-metadata", "tenacity"},
)


def render_constraints(lock: Path) -> str:
    """Pin the backend and PGO dependencies to their locked versions."""
    packages: dict[str, str] = {}
    for package in tomllib.loads(lock.read_text(encoding="utf-8"))["packages"]:
        name = package["name"]
        if name not in _BUILD_PACKAGES:
            continue
        version = package["version"]
        if name in packages and packages[name] != version:
            msg = f"multiple locked versions of {name}"
            raise ValueError(msg)
        packages[name] = version
    if missing := _BUILD_PACKAGES - packages.keys():
        msg = f"missing locked build packages: {', '.join(sorted(missing))}"
        raise ValueError(msg)
    return "".join(f"{name}=={version}\n" for name, version in sorted(packages.items()))


def main() -> None:
    """Write the temporary constraints used by cibuildwheel and uv build."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    arguments.output.write_text(render_constraints(arguments.lock), encoding="utf-8")


if __name__ == "__main__":
    main()


__all__ = [
    "render_constraints",
]
