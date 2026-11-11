# Subsystem: validate (_c/validate) — XSD/RELAX NG schema validation and HTML5 conformance.
from os import PathLike

from turbohtml.conformance import ConformanceMessage

from .dom import Node

def schema_compile(
    kind: int, source: str, base_url: str | None = None, include_root: str | PathLike[str] | None = None, /
) -> object: ...
def schema_validate(
    schema: object, node: Node, /, *, collect_errors: bool = True
) -> tuple[bool, list[tuple[str, str, int, str]]]: ...
def conformance_check(node: Node, /) -> tuple[bool, list[tuple[str, str, str, int, int]]]: ...
def conformance_filter(
    messages: tuple[ConformanceMessage, ...], severity: str, /
) -> tuple[ConformanceMessage, ...]: ...
