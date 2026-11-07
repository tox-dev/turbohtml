"""A canary file outside the resource root must never reach transform output, validation results or errors."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from turbohtml import parse_xml
from turbohtml.transform import Transform
from turbohtml.validate import RelaxNG

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

_XSL: Final[str] = 'xmlns:xsl="http://www.w3.org/1999/XSL/Transform"'
_RNG: Final[str] = 'xmlns="http://relaxng.org/ns/structure/1.0"'
# each entry turns the canary's location into an href that leaves the root one way: traversal, absolute paths, file
# URLs, percent-encoding, backslashes, a symlink placed inside the root, and an xml:base pointing outside
ESCAPES: Final[dict[str, Callable[[Path, str], tuple[str, str]]]] = {
    "parent": lambda _canary, name: (f"../outside/{name}", ""),
    "nested-parent": lambda _canary, name: (f"sub/../../outside/{name}", ""),
    "absolute": lambda canary, _name: (str(canary), ""),
    "file-url": lambda canary, _name: (canary.as_uri(), ""),
    "localhost-url": lambda canary, _name: ("file://localhost" + canary.as_uri().removeprefix("file://"), ""),
    "encoded-parent": lambda _canary, name: (f"%2e%2e/outside/{name}", ""),
    "backslash": lambda _canary, name: (f"..\\outside\\{name}", ""),
    "symlink": lambda _canary, name: (f"link-{name}", ""),
    "xml-base": lambda _canary, name: (name, "../outside/"),
    "unc": lambda _canary, name: (f"//localhost/outside/{name}", ""),
}


def main(argv: Sequence[str] | None = None) -> int:
    # Exit nonzero when any attempt exposes the canary.
    parser: Final = argparse.ArgumentParser(description="Probe resource-root containment with an outside canary.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cases", type=int, default=len(ESCAPES))
    parser.add_argument("--negative-control", action="store_true", help="place the canary inside the root")
    arguments: Final = parser.parse_args(argv)
    if not 1 <= arguments.cases <= 256:
        parser.error("--cases must be between 1 and 256")
    findings = 0
    for seed in range(arguments.seed, arguments.seed + arguments.cases):
        for verdict in compare(generate(seed), negative_control=arguments.negative_control):
            findings += verdict.leaked
            print(json.dumps(asdict(verdict), sort_keys=True))
    print(json.dumps({"summary": {"cases": arguments.cases, "findings": findings}}, sort_keys=True))
    return int(findings != 0)


def generate(seed: int) -> Attempt:
    # Draw an escape technique and a fresh canary token for one attempt.
    rng: Final = random.Random(seed)
    return Attempt(seed=seed, escape=rng.choice(sorted(ESCAPES)), token=f"canary{rng.getrandbits(64):016x}")


def compare(attempt: Attempt, *, negative_control: bool = False) -> Iterator[Verdict]:
    # Run the escape against both resolvers; a verdict leaks when the token shows up anywhere observable.
    with tempfile.TemporaryDirectory(prefix="resource-canary-") as scratch:
        root: Final = Path(scratch, "root")
        outside: Final = root if negative_control else Path(scratch, "outside")
        (root / "sub").mkdir(parents=True)
        outside.mkdir(exist_ok=True)
        for engine, name, payload, run in (
            ("xslt", "canary.xsl", _canary_stylesheet(attempt.token), _transform),
            ("relaxng", "canary.rng", f'<element {_RNG} name="{attempt.token}"><empty/></element>', _validate),
        ):
            canary = outside / name
            canary.write_text(payload)
            (root / f"link-{name}").symlink_to(canary)
            href, base = ESCAPES[attempt.escape](canary, name)
            if negative_control:
                href, base = name, ""
            observed = run(root, href, base, attempt.token)
            yield Verdict(
                seed=attempt.seed,
                escape=attempt.escape,
                engine=engine,
                href_sha256=hashlib.sha256(href.encode()).hexdigest(),
                leaked=attempt.token in observed,
            )


def _canary_stylesheet(token: str) -> str:
    return f'<xsl:stylesheet version="1.0" {_XSL}><xsl:template match="/">{token}</xsl:template></xsl:stylesheet>'


def _transform(root: Path, href: str, base: str, _token: str) -> str:
    principal: Final = (
        f'<xsl:stylesheet version="1.0" {_XSL}{_xml_base(base)}><xsl:import href="{_attribute(href)}"/>'
        '<xsl:output method="text"/></xsl:stylesheet>'
    )
    try:
        return Transform(parse_xml(principal), base_url=str(root / "main.xsl"), import_root=root)(parse_xml("<r/>"))
    except (ValueError, OSError) as error:
        return str(error)


def _validate(root: Path, href: str, base: str, token: str) -> str:
    schema: Final = f'<element {_RNG} name="r"{_xml_base(base)}><externalRef href="{_attribute(href)}"/></element>'
    try:
        compiled = RelaxNG(schema, base_url=str(root / "main.rng"), include_root=root)
    except (ValueError, OSError) as error:
        return str(error)
    # only the canary schema accepts an element named after the token, so acceptance shows it was read
    result: Final = compiled.validate(parse_xml(f"<r><{token}/></r>"))
    return token if result.valid else " ".join(error.message for error in result.errors)


def _xml_base(base: str) -> str:
    return f' xml:base="{_attribute(base)}"' if base else ""


def _attribute(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


@dataclass(frozen=True, slots=True)
class Attempt:
    # One escape technique with a token no other file on disk holds.
    seed: int
    escape: str
    token: str


@dataclass(frozen=True, kw_only=True, slots=True)
class Verdict:
    # Log the href only by hash: an escaping path may name private locations.
    seed: int
    escape: str
    engine: str
    href_sha256: str
    leaked: bool


__all__: Final = [
    "ESCAPES",
    "Attempt",
    "Verdict",
    "compare",
    "generate",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
