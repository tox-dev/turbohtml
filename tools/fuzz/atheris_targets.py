"""Missing qualified owners must stop the driver before it starts the native runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .atheris_content_targets import content_targets
from .atheris_dom_targets import dom_targets
from .atheris_driver import fuzz
from .atheris_generation_targets import generation_targets
from .atheris_javascript_targets import initialize_javascript, javascript_targets
from .atheris_parser_targets import parser_targets
from .atheris_reference_targets import reference_targets
from .atheris_registry import validate_owners
from .atheris_stylesheet_targets import stylesheet_targets

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .atheris_registry import Target

__all__ = ["MODULES", "main", "owner_inventory", "public_targets"]

MODULES: Final = (
    "turbohtml",
    "turbohtml.__main__",
    "turbohtml.build",
    "turbohtml.clean",
    "turbohtml.conformance",
    "turbohtml.convert",
    "turbohtml.cssom",
    "turbohtml.detect",
    "turbohtml.extract",
    "turbohtml.migration.bleach",
    "turbohtml.migration.markupsafe",
    "turbohtml.migration.stdlib",
    "turbohtml.mutations",
    "turbohtml.query",
    "turbohtml.rewrite",
    "turbohtml.saxparse",
    "turbohtml.transform",
    "turbohtml.traverse",
    "turbohtml.treebuild",
    "turbohtml.validate",
)


def main(argv: Sequence[str] | None = None) -> int:
    """LibFuzzer receives its flags after the target and corpus arguments."""
    parser: Final = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=tuple(target.name for target in public_targets()))
    parser.add_argument("--encoding", default="UTF-8")
    parser.add_argument("--sniff", action="store_true")
    parser.add_argument("--corpus", required=True, type=Path)
    parsed: Final = parser.parse_known_args(argv)
    arguments: Final = parsed[0]
    flags: Final = parsed[1]
    targets: Final = public_targets(arguments.encoding, sniff=arguments.sniff)
    if arguments.target == "javascript":
        initialize_javascript()
    arguments.corpus.mkdir(parents=True, exist_ok=True)
    manifest: Final = {
        "target": arguments.target,
        "exports": sorted(export for export, owner in owner_inventory().items() if owner == arguments.target),
        "corpus": str(arguments.corpus),
    }
    if arguments.target == "encoding-bytes":
        manifest.update(encoding=arguments.encoding, sniff=arguments.sniff)
    arguments.corpus.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    fuzz(targets, MODULES, arguments.target, (sys.argv[0], str(arguments.corpus), *flags))
    return 0


def public_targets(encoding: str = "UTF-8", *, sniff: bool = False) -> tuple[Target, ...]:
    """Each group owns separate modules and qualified re-export aliases."""
    targets: Final = (
        parser_targets()
        + reference_targets()
        + content_targets()
        + dom_targets()
        + stylesheet_targets()
        + javascript_targets()
        + generation_targets(encoding, sniff=sniff)
    )
    validate_owners(targets, MODULES)
    return targets


def owner_inventory() -> dict[str, str]:
    """Read executable consumers rather than assigning ownership from mode names."""
    return {export: target.name for export, target in validate_owners(public_targets(), MODULES).items()}


if __name__ == "__main__":
    raise SystemExit(main())
