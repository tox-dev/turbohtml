"""The XML sanitizer run over DOMPurify's own XSS corpus: every vector must serialize to inert, well-formed XML.

DOMPurify (https://github.com/cure53/DOMPurify) is the reference HTML sanitizer; its ``test/fixtures/expect.mjs`` is
~219 ``{payload}`` attack vectors, vendored as the pinned submodule ``tests/conformance/DOMPurify``. The head-to-head
comparison against a live DOMPurify build lives in ``test_sanitizer_dompurify_conformance.py``; this suite asks the one
question the XML output mode adds. For every vector, ``Policy(xml=True)`` must produce a string that (1) reparses
through :func:`turbohtml.parse_xml` -- it is well-formed XML, the guarantee the mode exists to give -- and (2) is inert
when reparsed as markup: no scriptable HTML element, no event-handler attribute, no scripting URL survived the walk into
the XML serialization. The corpus is a data submodule, so its absence is a setup error, not a skip (the dedicated
conformance CI job checks it out); no Node oracle is needed here, only the fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from turbohtml import Element, parse_fragment, parse_xml
from turbohtml.clean import Policy, sanitize

_FIXTURE = Path(__file__).parent / "DOMPurify" / "test" / "fixtures" / "expect.mjs"
if not _FIXTURE.exists():  # pragma: no cover
    msg = (
        "submodule tests/conformance/DOMPurify not checked out; "
        "run: git submodule update --init tests/conformance/DOMPurify"
    )
    raise RuntimeError(msg)


def _load_payloads() -> list[str]:
    """Extract every ``payload`` string from the ESM fixture by decoding each object with the JSON reader."""
    body = _FIXTURE.read_text(encoding="utf-8").split("export default", 1)[1]
    decoder = json.JSONDecoder()
    index = body.index("[") + 1
    payloads: list[str] = []
    while True:
        while body[index] in " \t\r\n,":
            index += 1
        if body[index] == "]":
            return payloads
        entry, index = decoder.raw_decode(body, index)
        payloads.append(entry["payload"])


_PAYLOADS = _load_payloads()
_IDS = [f"{position:03d}" for position in range(len(_PAYLOADS))]

# A permissive allowlist so most of each vector flows through to the serializer: the non-configurable safety baseline,
# not a narrow tag set, is what must keep the output inert, and svg/math are kept so foreign vectors are exercised too.
_XML = Policy(
    tags=frozenset({
        "a", "b", "i", "em", "strong", "p", "div", "span", "br", "img", "ul", "ol", "li", "table", "tr", "td",
        "svg", "rect", "circle", "g", "use", "math", "mi", "mo", "mtext", "annotation-xml", "foreignObject",
        "form", "input", "button", "label", "style", "title", "h1", "code", "pre",
    }),
    attributes={"*": frozenset({"*"})},
    xml=True,
)  # fmt: skip

_DANGER_TAGS = frozenset({"script", "iframe", "object", "embed", "base", "noscript"})
_SCRIPTING_SCHEMES = ("javascript:", "vbscript:", "data:text/html")
_URL_ATTRS = frozenset({"href", "src", "xlink:href"})
# The ASCII tab/newline a browser removes from a URL before scheme detection, and the leading C0-control-or-space it
# then trims: only after both does a scheme resolve, so a leading U+00A0/U+2028 (neither) leaves a URL relative, hence
# safe. Mirroring that trim is what keeps the check from flagging the exact vectors DOMPurify and turbohtml both keep.
_URL_STRIP_ANY = "\t\n\r"
_URL_TRIM_LEADING = "".join(chr(code) for code in range(0x21))


def _attr_value(raw: object) -> str:
    return raw if isinstance(raw, str) else ""


def _scheme_is_scripting(value: str) -> bool:
    for character in _URL_STRIP_ANY:
        value = value.replace(character, "")
    return value.lstrip(_URL_TRIM_LEADING).lower().startswith(_SCRIPTING_SCHEMES)


def _survivors(xml: str) -> list[str]:
    """Reparse the XML output as markup and list every executable construct still present; empty means inert."""
    survived: list[str] = []
    stack: list[object] = list(parse_fragment(xml).children)
    while stack:
        node = stack.pop()
        if not isinstance(node, Element):
            continue
        if node.tag in _DANGER_TAGS and node.namespace.value == "html":
            survived.append(f"<{node.tag}>")
        for name, raw in node.attrs.items():
            lowered = name.lower()
            if lowered.startswith("on"):
                survived.append(f"@{lowered}")
            elif lowered in _URL_ATTRS and _scheme_is_scripting(_attr_value(raw)):
                survived.append(f"{lowered}=scripting")
        stack.extend(node.children)
    return survived


@pytest.mark.parametrize("payload", _PAYLOADS, ids=_IDS)
def test_every_vector_is_inert_and_well_formed(payload: str) -> None:
    out = sanitize(payload, _XML)
    parse_xml(f"<root>{out}</root>")
    assert _survivors(out) == []


def test_corpus_is_not_silently_empty() -> None:
    assert len(_PAYLOADS) >= 200
