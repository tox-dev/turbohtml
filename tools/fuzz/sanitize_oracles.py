"""
Wrong-output oracles for ``turbohtml.clean.sanitize``: the ``--mode oracle`` lane of ``tools/fuzz/fuzz.py``.

A sanitizer bug usually returns unsafe or altered markup without crashing, so each oracle compares the sanitizer with
code it does not share. ``mutation`` re-parses the output with the vendored html5lib-python and requires the tree the
sanitizer judged; ``policy`` requires that re-parsed tree to obey the random Policy that produced it; ``url`` compares
each keep or drop of a URL attribute with the scheme turbohtml's WHATWG ``normalize_url`` reads from the decoded value.
A negative control proves every oracle still fires before the run starts. The run minimizes each divergence and
re-checks the re-parse ones with parse5, which tells a stale html5lib rule apart from a turbohtml mutation.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import html
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeVar
from urllib.parse import quote

import html5lib
from html5lib.constants import namespaces

from turbohtml import Comment, Element, Namespace, Node, Text, parse_fragment
from turbohtml.clean import DEFAULT_CSS_PROPERTIES, OnDisallowed, Policy, sanitize, sanitize_node
from turbohtml.extract import normalize_url

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from xml.dom.minidom import DocumentFragment as DomFragment
    from xml.dom.minidom import Element as DomElement

_T = TypeVar("_T")
_Check = Callable[[str], str | None]
_PolicyValue = frozenset[str] | Mapping[str, frozenset[str]] | bool | OnDisallowed

_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_DATA: Final[Path] = _ROOT / "tools" / "fuzz-data"
_NODE_DIR: Final[Path] = _ROOT / "tools" / "bench" / "node"
# sanitize(str) parses its input as a <div> fragment with scripting off (clean/sanitize.c, th_tree_parse_fragment)
_CONTEXT: Final = "div"
# TH_MAX_TREE_DEPTH (dom/tree.h): past it turbohtml, Blink and Gecko flatten nesting while html5lib and parse5 do not
_DEPTH_CAP: Final = 512
# meriyah's MINIMUM_COMPARED_RATIO: a run that skips more than this share compared too little to mean anything
_MIN_COMPARED_RATIO: Final = 0.9
_MINIMIZE_BUDGET: Final = 1500
_DOM_BUILDER: Final = html5lib.getTreeBuilder("dom")
_NAMESPACE_PREFIX: Final[dict[str, str]] = {namespaces["svg"]: "svg", namespaces["mathml"]: "math"}
_C0_OR_SPACE: Final = "".join(map(chr, range(0x21)))
_SCHEME: Final = re.compile(r"([a-z][a-z0-9+.-]*):")
_UNSAFE_HTML: Final = frozenset({
    "script",
    "iframe",
    "embed",
    "object",
    "noscript",
    "noembed",
    "noframes",
    "base",
    "basefont",
    "title",
    "plaintext",
    "xmp",
    "template",
})
_SVG_ANIMATION: Final = frozenset({"animate", "animateColor", "animateMotion", "animateTransform", "set"})
_URL_ATTRS: Final = frozenset({
    "href",
    "src",
    "action",
    "formaction",
    "xlink:href",
    "poster",
    "background",
    "cite",
    "data",
    "ping",
    "longdesc",
})
_CSS_DANGER: Final = ("javascript:", "vbscript:", "expression(", "behavior:", "-moz-binding")
# the code points sanitize.c is_url_ignorable skips besides C0, space and DEL
_URL_IGNORABLE: Final = frozenset("\u00ad\u200b\u200c\u200d\u2060\ufeff")
_FAILING_ORACLES: Final = frozenset({"mutation", "string-path", "policy", "url-unsafe", "decode"})


def main() -> int:
    """Return 0 when every oracle agrees, 1 on a failing finding, 2 when the run proved nothing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=1.0, help="generation budget after the seed pass")
    parser.add_argument("--rng-seed", type=int, default=0)
    parser.add_argument("--repro", type=Path, required=True, help="each input is written here before it runs")
    parser.add_argument("--report", type=Path, required=True, help="JSON file receiving every finding with its repro")
    args = parser.parse_args()

    if blind := [name for name, fired in _negative_controls() if not fired]:
        print(f"BLIND ORACLE: the negative control did not fire for {', '.join(blind)}", file=sys.stderr)
        return 2
    rng = random.Random(args.rng_seed)
    run = _Run(args.repro)
    seeds = _seeds(rng)
    for markup in seeds:
        for policy in (Policy(), Policy.relaxed(), _draw_policy(rng)):
            run.markup(markup, policy)
    for wpt_markup, wpt_policy in _wpt_cases():
        run.markup(wpt_markup, wpt_policy)
    tokens = _dictionary_tokens()
    deadline = time.monotonic() + args.minutes * 60
    while time.monotonic() < deadline:
        if (draw := rng.random()) < 0.5:
            run.markup(_mutagen(rng, seeds), _draw_policy(rng))
        elif draw < 0.75:
            run.markup(_splice(rng, rng.choice(seeds), tokens), _draw_policy(rng))
        else:
            run.url(*_url_case(rng))
    print(f"oracle: {dict(run.stats)}")
    if run.stats["compared"] < _MIN_COMPARED_RATIO * (run.stats["compared"] + run.stats["skipped"]):
        print(f"VACUOUS RUN: compared below {_MIN_COMPARED_RATIO:.0%} of attempted cases", file=sys.stderr)
        return 2
    findings = _confirm_with_parse5(run.minimized())
    args.report.write_text(json.dumps([dataclasses.asdict(finding) for finding, _ in findings], indent=2) + "\n")
    for finding, _ in findings:
        # only a hash reaches the log: the repro may be an unreported bypass (DESIGN-v2 decision D5)
        digest = hashlib.sha256(finding.signature.encode()).hexdigest()[:12]
        print(f"FINDING {finding.oracle} {digest} x{finding.hits} parse5={finding.parse5}", file=sys.stderr)
    print(f"{len(findings)} finding(s) written to {args.report}")
    return int(any(finding.oracle in _FAILING_ORACLES for finding, _ in findings))


def _negative_controls() -> Iterator[tuple[str, bool]]:
    """
    Feed each oracle a known-bad output and report whether it fired.

    Modeled on DOMPurify's ``ADD_ATTR: ['onerror']`` control (test/fuzz/sanitize.fast-check.js:357-376) and Fuzzilli's
    startup tests: an oracle that stops detecting fails the run instead of passing silently.
    """
    escaped = parse_fragment("<p>&lt;img src=x onerror=alert(1)&gt;</p>", _CONTEXT, positions=False)
    # a serializer that forgets to escape text, the SI7 class of MutaGen's detectors
    yield "mutation", _mutation_detail(escaped, html.unescape(escaped.inner_html)) is not None
    select = parse_fragment("<select><option>&lt;b&gt;x</option></select>", _CONTEXT, positions=False)
    yield "mutation in select", _mutation_detail(select, html.unescape(select.inner_html)) is not None
    yield "policy on*", "event handler img@onerror" in _violations("<img onerror=alert(1)>", Policy())
    yield "policy url", "url a@href" in _violations('<a href="java&#x09;script:alert(1)">x</a>', Policy())
    animation = Policy(tags=frozenset({"svg", "animate"}), attributes={"*": frozenset({"attributeName", "to"})})
    yield "policy baseline", "baseline element svg:animate" in _violations("<svg><animate to=x>", animation)
    yield "policy css", "dangerous css in b@style" in _violations('<b style="x:expr/**/ession(1)">', Policy())
    yield "url unsafe keep", _url_verdict("javascript:alert(1)", kept=True, policy=Policy()) == "url-unsafe"
    yield "url strict drop", _url_verdict("https://example.com/", kept=False, policy=Policy()) == "url-strict"
    relative = Policy(allow_relative_urls=True)
    yield (
        "url hidden scheme drop is not strict",
        _url_verdict("java\u200bscript:x", kept=False, policy=relative) is None,
    )
    yield (
        "url hidden scheme keep is unsafe",
        _url_verdict("java\u200bscript:x", kept=True, policy=relative) == "url-unsafe",
    )
    yield "css selector prelude is inert", not _css_danger("javascript:{}")
    yield "css import prelude is live", _css_danger("@import url(javascript:x);")
    yield "css declaration is live", _css_danger("a{b:url(javascript:x)}")


def _mutation_detail(tree: Node, serialized: str) -> str | None:
    """
    Describe where the independent re-parse of ``serialized`` first departs from ``tree``, or return None.

    The description names node shapes, not their text, so divergences that differ only in payload share a signature.
    """
    expected = _normalized_dump(tree)
    # html5lib-python and parse5 8.0 predate the customizable <select> parsing the pinned WPT corpus expects
    # (tests/conformance/data/wpt_html_tree.json keeps <select><div>), so only turbohtml can re-parse a select
    if any(element.tag == "select" and element.namespace is Namespace.HTML for element in _elements(tree)):
        reference = "turbohtml-reparse"
        reparsed = _normalized_dump(parse_fragment(serialized, _CONTEXT, positions=False, scripting=True))
    else:
        reference = "html5lib"
        reparsed = _dump_html5lib(_reparse(serialized))
    if expected == reparsed:
        return None
    return next(
        f"turbohtml {_shape(want)} != {reference} {_shape(got)}"
        for want, got in zip([*expected.split("\n"), None], [*reparsed.split("\n"), None], strict=False)
        if want != got
    )


def _normalized_dump(tree: Node) -> str:
    # the serialization algorithm emits CR as is and input preprocessing turns it into LF, so no serializer keeps it
    return "\n".join(_dump_turbohtml(tree.children)).replace("\r\n", "\n").replace("\r", "\n")


def _dump_turbohtml(nodes: Sequence[Node], depth: int = 0) -> list[str]:
    """
    Render turbohtml nodes in the html5lib-tests tree format.

    Modeled on tests/dom/test_treebuilder_conformance.py ``_dump_node``. Adjacent text merges and empty text drops,
    since no parser produces either and html5lib's ``testSerializer`` normalizes them away.
    """
    pad = "| " + "  " * depth
    out: list[str] = []
    pending_text = ""
    for node in nodes:
        if isinstance(node, Text):
            pending_text += node.data
            continue
        if pending_text:
            out.append(f'{pad}"{pending_text}"')
            pending_text = ""
        if isinstance(node, Element):
            foreign = node.namespace is not Namespace.HTML
            out.append(f"{pad}<{node.namespace.value + ' ' if foreign else ''}{node.tag}>")
            out.extend(
                sorted(
                    f'{pad}  {_attr_name(name, foreign=foreign)}="{_attr_text(value)}"'
                    for name, value in node.attrs.items()
                )
            )
            if not foreign and node.tag == "template":
                out.append(f"{pad}  content")
                out.extend(_dump_turbohtml(node.children[0].children, depth + 2))
            else:
                out.extend(_dump_turbohtml(node.children, depth + 1))
        elif isinstance(node, Comment):
            out.append(f"{pad}<!-- {node.data} -->")
    if pending_text:
        out.append(f'{pad}"{pending_text}"')
    return out


def _attr_name(name: str, *, foreign: bool) -> str:
    """Spell a namespaced attribute on a foreign element as prefix and local name apart, the .dat way."""
    return name.replace(":", " ", 1) if foreign and name.startswith(("xlink:", "xml:", "xmlns:")) else name


def _attr_text(value: str | list[str] | None) -> str:
    return " ".join(value) if isinstance(value, list) else value or ""


def _elements(root: Node) -> Iterator[Element]:
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, Element):
            yield node
        stack.extend(node.children)


def _reparse(serialized: str) -> DomFragment:
    """Parse ``serialized`` the way ``innerHTML`` assignment does: a <div> fragment with scripting on."""
    parser = html5lib.HTMLParser(tree=_DOM_BUILDER, namespaceHTMLElements=False)
    return parser.parseFragment(serialized, container=_CONTEXT, scripting=True)


def _dump_html5lib(fragment: DomFragment) -> str:
    """
    Render an html5lib tree in the html5lib-tests format with html5lib's own ``testSerializer``.

    It nests fragment children one column deeper than the .dat files, the offset ``convertTreeDump`` strips in
    html5lib/tests/tree_construction.py; continuation lines of a multi-line text node carry no bar and stay as they are.
    """
    lines = _DOM_BUILDER(namespaceHTMLElements=False).testSerializer(fragment).split("\n")[1:]
    return "\n".join("| " + line[3:] if line.startswith("|") else line for line in lines)


def _shape(line: str | None) -> str:
    if line is None:
        return "#end"
    body = line.lstrip("| ")
    if not line.startswith("|") or body.startswith('"'):
        return "#text"  # a line without the bar continues a multi-line text node
    if body.startswith("<!--"):
        return "#comment"
    return body.split(">", 1)[0] + ">" if body.startswith("<") else "@" + body.split("=", 1)[0]


def _violations(serialized: str, policy: Policy) -> list[str]:
    """
    List every way the independent re-parse of ``serialized`` breaks ``policy`` or the non-configurable baseline.

    Modeled on OWASP java-html-sanitizer's ``checkSafe`` (HtmlPolicyBuilderFuzzerTest.java:123-155), which walks the
    validator.nu re-parse, generalized from its one fixed policy to the Policy under test.
    """
    found: list[str] = []
    stack: list[DomElement] = [node for node in _reparse(serialized).childNodes if node.nodeType == node.ELEMENT_NODE]
    while stack:
        element = stack.pop()
        found += _element_violations(element, policy) + _attribute_violations(element, policy)
        stack.extend(node for node in element.childNodes if node.nodeType == node.ELEMENT_NODE)
    return found


def _element_violations(element: DomElement, policy: Policy) -> list[str]:
    namespace = _NAMESPACE_PREFIX.get(element.namespaceURI or "", "html")
    tag = element.nodeName
    gate = {"html": policy.allow_html, "svg": policy.allow_svg, "math": policy.allow_mathml}[namespace]
    found: list[str] = []
    if tag not in policy.tags or not gate:
        found.append(f"element {namespace}:{tag} not allowed")
    if (namespace, tag) in _BASELINE_ELEMENTS:
        found.append(f"baseline element {namespace}:{tag}")
    if tag == "style" and _css_danger(
        "".join(node.data for node in element.childNodes if node.nodeType == node.TEXT_NODE)
    ):
        found.append(f"dangerous css in {namespace}:style")
    return found


_BASELINE_ELEMENTS: Final = frozenset({
    *(("html", tag) for tag in _UNSAFE_HTML),
    *(("svg", tag) for tag in _SVG_ANIMATION),
    ("svg", "script"),
})


def _css_danger(text: str) -> bool:
    """
    Whether CSS text still carries a script-capable construct once escapes and comments are resolved.

    The tokens are the ones the DOMPurify conformance suite's ``_live_danger`` scans for; decoding escapes first covers
    lxml_html_clean CVE-2026-28348, which hid ``@import`` behind CSS unicode escapes.
    """
    decoded = re.sub(
        r"\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?|\\(.)",
        lambda match: chr(min(int(match[1], 16), 0x10FFFF) or 0xFFFD) if match[1] else match[2],
        text,
    )
    flat = re.sub(r"\s+", "", re.sub(r"/\*.*?(?:\*/|$)", "", decoded, flags=re.DOTALL)).lower()
    # a style rule's prelude is a selector, which matches nothing when invalid (Selectors 4 3.9) and runs no script, so
    # only declarations and at-rule preludes such as @import url(...) can carry a live token
    flat = re.sub(r"(^|[;{}])[^@;{}][^;{}]*(?=\{)", r"\1", flat)
    return any(token in flat for token in _CSS_DANGER)


def _attribute_violations(element: DomElement, policy: Policy) -> list[str]:
    tag = element.nodeName
    allowed = policy.attributes.get(tag, frozenset()) | policy.attributes.get("*", frozenset())
    if tag == "a" and policy.add_link_rel:
        allowed |= {"rel"}  # add_link_rel forces rel onto every kept <a href>
    found: list[str] = []
    for name, value in element.attributes.items():
        if "*" not in allowed and name not in allowed and not name.startswith(tuple(policy.attribute_prefixes)):
            found.append(f"attribute {tag}@{name} not allowed")
        if name.lower().startswith("on"):
            found.append(f"event handler {tag}@{name}")
        if name.lower() in _URL_ATTRS and _whatwg_allows(value, policy) is False:
            found.append(f"url {tag}@{name}")
        if name.lower() == "style" and _css_danger(value):
            found.append(f"dangerous css in {tag}@style")
    return found


def _whatwg_allows(value: str, policy: Policy) -> bool | None:
    """
    Decide keep or drop for a URL attribute value from the scheme the WHATWG URL parser reads.

    ``normalize_url`` applies the WHATWG scheme rules and lowercases the scheme, so a scheme the parser reads leads its
    output. None means the URL engine rejects the value, so a browser never fetches it and either decision is safe.
    """
    try:
        normalized = normalize_url(value)
    except ValueError:
        return None
    if (scheme := _SCHEME.match(normalized)) is not None:
        return scheme[1] != "javascript" and scheme[1] in policy.url_schemes
    # the WHATWG parser trims C0 and space at both ends and drops every tab and newline before reading the URL
    trimmed = value.strip(_C0_OR_SPACE).replace("\t", "").replace("\n", "").replace("\r", "")
    if not (policy.allow_relative_urls or (policy.allow_fragment_urls and trimmed.startswith("#"))):
        return False
    # the documented hidden-scheme rule (docs/explanation/sanitizing.rst) also refuses a relative value that spells a
    # refused scheme once its URL-ignorable and non-ASCII code points are dropped, as DOMPurify and bleach do
    hidden = _hidden_scheme(value)
    return hidden is None or (hidden != "javascript" and hidden in policy.url_schemes)


def _hidden_scheme(value: str) -> str | None:
    """Mirror sanitize.c ``hidden_scheme_allowed``: the scheme a value spells without ignorable or non-ASCII points."""
    scheme: list[str] = []
    for character in value:
        if character in _URL_IGNORABLE or ord(character) <= 0x20 or ord(character) == 0x7F or ord(character) >= 0x80:
            continue
        if character == ":" and scheme:
            return "".join(scheme).lower()
        if not (character.isascii() and (character.isalnum() or character in "+-.") if scheme else character.isalpha()):
            return None
        scheme.append(character)
    return None


def _url_verdict(decoded: str, *, kept: bool, policy: Policy) -> str | None:
    if (expected := _whatwg_allows(decoded, policy)) is None or expected == kept:
        return None
    return "url-unsafe" if kept else "url-strict"


class _Run:
    def __init__(self, repro: Path) -> None:
        self.repro = repro
        self.stats: Counter[str] = Counter()
        self.hits: Counter[str] = Counter()
        self.found: dict[str, tuple[_Finding, Policy, _Check | None]] = {}

    def markup(self, markup: str, policy: Policy) -> None:
        self.repro.write_text(markup, encoding="utf-8", errors="surrogatepass")
        tree = sanitize_node(parse_fragment(markup, _CONTEXT, positions=False), policy)
        if _depth(tree) >= _DEPTH_CAP:
            self.stats["skipped"] += 1
            return
        self.stats["compared"] += 1
        if (serialized := tree.inner_html) != sanitize(markup, policy):
            self._record(
                "string-path", "sanitize(str) != sanitize_node().inner_html", markup, policy, _string_path_check(policy)
            )
        if (detail := _mutation_detail(tree, serialized)) is not None:
            self._record("mutation", detail, markup, policy, _mutation_check(policy))
        for violation in _violations(serialized, policy):
            self._record("policy", violation, markup, policy, _policy_check(policy, violation))

    def url(self, element: str, attribute: str, recipe: str, source: str, policy: Policy) -> None:
        markup = (
            f'<svg><a {attribute}="{source}">x</a></svg>'
            if element == "svg"
            else f'<{element} {attribute}="{source}">x</{element}>'
        )
        self.repro.write_text(markup, encoding="utf-8", errors="surrogatepass")
        self.stats["compared"] += 1
        decoded = _attr_text(_target(parse_fragment(markup, _CONTEXT, positions=False).children).attrs.get(attribute))
        if decoded != _dom_target(_reparse(markup)).getAttribute(attribute):
            self._record("decode", recipe, markup, policy, None)
        kept = (
            attribute
            in _target(sanitize_node(parse_fragment(markup, _CONTEXT, positions=False), policy).children).attrs
        )
        if (verdict := _url_verdict(decoded, kept=kept, policy=policy)) is not None:
            self._record(verdict, recipe, markup, policy, None)

    def minimized(self) -> list[tuple[_Finding, Policy]]:
        return [
            (
                dataclasses.replace(
                    finding, markup=markup, sanitized=sanitize(markup, policy), hits=self.hits[signature]
                ),
                policy,
            )
            for signature, (finding, policy, check) in self.found.items()
            for markup in [finding.markup if check is None else _ddmin(finding.markup, check, finding.detail)]
        ]

    def _record(self, oracle: str, detail: str, markup: str, policy: Policy, check: _Check | None) -> None:
        signature = f"{oracle}: {detail}"
        self.hits[signature] += 1
        if signature not in self.found:
            self.found[signature] = (_Finding(oracle, signature, markup, _policy_repr(policy), detail), policy, check)


def _depth(root: Node) -> int:
    # a loop, not recursion: a seed nested near the cap would exhaust Python's recursion limit
    deepest = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        deepest = max(deepest, depth)
        stack.extend((child, depth + 1) for child in node.children if isinstance(child, Element))
    return deepest


def _string_path_check(policy: Policy) -> _Check:
    def check(markup: str) -> str | None:
        tree = sanitize_node(parse_fragment(markup, _CONTEXT, positions=False), policy)
        return None if tree.inner_html == sanitize(markup, policy) else "sanitize(str) != sanitize_node().inner_html"

    return check


@dataclass(frozen=True, slots=True)
class _Finding:
    oracle: str
    signature: str
    markup: str
    policy: str
    detail: str
    sanitized: str = ""
    hits: int = 1
    parse5: str = "n/a"


def _policy_repr(policy: Policy) -> str:
    """Spell ``policy`` as a constructor call so a finding's repro rebuilds it; default fields are left out."""
    default = Policy()
    return "Policy({})".format(
        ", ".join(
            f"{field.name}={_value_repr(value)}"
            for field in dataclasses.fields(Policy)
            if (value := getattr(policy, field.name)) != getattr(default, field.name)
        )
    )


def _value_repr(value: _PolicyValue) -> str:
    if isinstance(value, OnDisallowed):
        return f"OnDisallowed.{value.name}"
    if isinstance(value, frozenset):
        return f"frozenset({sorted(value)!r})"
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{key!r}: {_value_repr(item)}" for key, item in sorted(value.items())) + "}"
    return repr(value)


def _mutation_check(policy: Policy) -> _Check:
    def check(markup: str) -> str | None:
        tree = sanitize_node(parse_fragment(markup, _CONTEXT, positions=False), policy)
        return None if _depth(tree) >= _DEPTH_CAP else _mutation_detail(tree, tree.inner_html)

    return check


def _policy_check(policy: Policy, violation: str) -> _Check:
    def check(markup: str) -> str | None:
        return violation if violation in _violations(sanitize(markup, policy), policy) else None

    return check


def _target(nodes: Sequence[Node]) -> Element:
    element = next(node for node in nodes if isinstance(node, Element))
    return _target(element.children) if element.tag == "svg" else element


def _dom_target(parent: DomFragment | DomElement) -> DomElement:
    element = next(node for node in parent.childNodes if node.nodeType == node.ELEMENT_NODE)
    return _dom_target(element) if element.nodeName == "svg" else element


def _ddmin(markup: str, check: _Check, detail: str) -> str:
    """
    Shrink ``markup`` to an input that still yields ``detail``, with Zeller and Hildebrandt's ddmin.

    Written, not reused: picire 21.8, the maintained Python ddmin, imports ``pkg_resources`` and fails to load on 3.14.
    """
    calls = 0
    granularity = 2
    while len(markup) >= 2 and calls < _MINIMIZE_BUDGET:
        chunk = max(len(markup) // granularity, 1)
        for start in range(0, len(markup), chunk):
            candidate = markup[:start] + markup[start + chunk :]
            calls += 1
            if check(candidate) == detail:
                markup = candidate
                granularity = max(granularity - 1, 2)
                break
        else:
            if chunk == 1:
                break
            granularity = min(granularity * 2, len(markup))
    return markup


def _seeds(rng: random.Random) -> list[str]:
    """Collect the vendored payload corpora; a missing one is a setup error, never a silent skip."""
    corpus = sorted((_ROOT / "tools" / "fuzz" / "corpus" / "sanitize").glob("*"))
    return list(
        dict.fromkeys([
            *_dompurify_payloads(),
            *_h5sc_vectors(),
            *_dharma_payloads(rng),
            *(path.read_text(encoding="utf-8") for path in corpus),
        ])
    )


def _dompurify_payloads() -> list[str]:
    """
    Read the ``payload`` strings of DOMPurify's ESM fixture as JSON objects.

    Mirrors ``_load_payloads`` in tests/conformance/test_sanitizer_dompurify_conformance.py.
    """
    fixture = _ROOT / "tests" / "conformance" / "DOMPurify" / "test" / "fixtures" / "expect.mjs"
    body = _require(fixture).read_text(encoding="utf-8").split("export default", 1)[1]
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


def _require(path: Path) -> Path:
    if not path.exists():
        msg = f"{path} is missing; tox -e fuzz-oracle checks out the seed submodules"
        raise FileNotFoundError(msg)
    return path


def _h5sc_vectors() -> list[str]:
    """Unwrap the H5SC vectors from the ``<div id=N>…//["'`-->]]>]</div>`` shell vectors.txt puts around each."""
    text = _require(_DATA / "h5sc" / "vectors.txt").read_text(encoding="utf-8")
    return re.findall(r'<div id="\d+">(.*?)//\["\'`-->\]\]>\]</div>', text, flags=re.DOTALL)


def _dharma_payloads(rng: random.Random) -> list[str]:
    """
    Generate payloads from Dharma's ``xss.dg`` grammar with the installed dharma package, unchanged.

    The child process takes a seed drawn from ``rng``, so the batch is reproducible from ``--rng-seed``.
    """
    if (spec := importlib.util.find_spec("dharma")) is None or spec.origin is None:
        msg = "the dharma package, which ships xss.dg, is missing; tox -e fuzz-oracle installs it"
        raise ModuleNotFoundError(msg)
    grammar = Path(spec.origin).parent / "grammars" / "xss.dg"
    seed = str(rng.randrange(2**31))
    command = [
        sys.executable,
        "-m",
        "dharma",
        "-grammars",
        str(grammar),
        "-count",
        "200",
        "-logging",
        "40",
        "-seed",
        seed,
    ]
    output = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    return [line for line in output.splitlines() if line.strip()]


def _draw_policy(rng: random.Random) -> Policy:
    """
    Draw a Policy from a base allowlist plus the hazard pool most sanitizer bypasses need.

    The pool is DESIGN-v2 §4d's: foreign content, raw-text and RCDATA elements, SVG animation, ``style``, URL-bearing
    attributes and custom schemes, since 11 of MutaGen's 16 bypasses needed a relaxed configuration.
    """
    tags = {
        *rng.sample(sorted(Policy.relaxed().tags), rng.randint(0, 12)),
        *rng.sample(_HAZARD_TAGS, rng.randint(1, 8)),
    }
    return Policy(
        tags=frozenset(tags),
        attributes={
            "*": frozenset(rng.sample(_HAZARD_ATTRS, rng.randint(0, 6))),
            **{
                tag: frozenset(rng.sample(_HAZARD_ATTRS, rng.randint(1, 4)))
                for tag in rng.sample(sorted(tags), min(3, len(tags)))
            },
        },
        url_schemes=frozenset(rng.sample(_SCHEMES, rng.randint(0, 4))),
        allow_relative_urls=rng.random() < 0.5,
        allow_fragment_urls=rng.random() < 0.5,
        on_disallowed_tag=rng.choice(list(OnDisallowed)),
        strip_comments=rng.random() < 0.5,
        remove_with_content=frozenset(rng.sample(_HAZARD_TAGS, rng.randint(0, 3))),
        css_properties=DEFAULT_CSS_PROPERTIES | {"background-image", "list-style-image", "behavior"},
        attribute_prefixes=frozenset({"data-"}) if rng.random() < 0.3 else frozenset(),
        allow_html=rng.random() < 0.9,
        allow_svg=rng.random() < 0.8,
        allow_mathml=rng.random() < 0.8,
    )


_HAZARD_TAGS: Final = (
    "svg",
    "math",
    "annotation-xml",
    "foreignObject",
    "desc",
    "mtext",
    "mglyph",
    "mi",
    "template",
    "xmp",
    "textarea",
    "title",
    "select",
    "option",
    "animate",
    "set",
    "style",
    "noscript",
    "iframe",
    "a",
    "form",
    "button",
    "img",
    "image",
    "use",
    "table",
    "noembed",
    "noframes",
    "listing",
    "pre",
    "plaintext",
)
_HAZARD_ATTRS: Final = (
    *sorted(_URL_ATTRS),
    "style",
    "onerror",
    "onclick",
    "attributeName",
    "from",
    "to",
    "values",
    "encoding",
    "id",
    "name",
    "title",
    "class",
    "is",
    "rel",
    "type",
)
_SCHEMES: Final = ("http", "https", "mailto", "data", "javascript", "vbscript", "ftp", "tel", "x-custom")


def _wpt_cases() -> Iterator[tuple[str, Policy]]:
    """
    Yield each WPT sanitizer-api ``#data`` input with its ``#config`` mapped onto a Policy.

    The block split follows tools/generate_wpt_tree_corpus.py ``_parse_file``. Only ``elements`` and ``attributes`` have
    a Policy field to map to; a case naming neither runs under the default Policy.
    """
    for path in sorted(_require(_DATA / "wpt" / "sanitizer-api").glob("*.sub.dat")):
        for block in path.read_text(encoding="utf-8").split("#data\n")[1:]:
            data, _, rest = block.partition("\n#")
            config = json.loads(rest.partition("\n")[2].partition("\n#")[0]) if rest.startswith("config\n") else {}
            tags = [entry if isinstance(entry, str) else entry["name"] for entry in config.get("elements", [])]
            attributes = [entry if isinstance(entry, str) else entry["name"] for entry in config.get("attributes", [])]
            yield (
                data.replace("{{host}}", "example.com"),
                Policy(
                    tags=frozenset(tags) if tags else Policy().tags,
                    attributes={"*": frozenset(attributes)} if attributes else Policy().attributes,
                ),
            )


def _dictionary_tokens() -> list[str]:
    r"""
    Read the google/fuzzing html, svg, mathml and css AFL dictionaries, the set jsoup concatenates into combo.dict.

    Entries are ``name="value"`` lines with ``\\``, ``\"`` and ``\xNN`` escapes, which AFL's ``load_extras_file``
    decodes the same way.
    """
    return [
        re.sub(
            r"\\(x[0-9a-fA-F]{2}|.)",
            lambda escape: chr(int(escape[1][1:], 16)) if len(escape[1]) == 3 else escape[1],
            match[1],
        )
        for name in ("html", "svg", "mathml", "css")
        for line in _require(_DATA / "google-fuzzing" / "dictionaries" / f"{name}.dict")
        .read_text(encoding="utf-8")
        .splitlines()
        if not line.lstrip().startswith("#") and (match := re.search(r'"(.*)"\s*$', line)) is not None
    ]


def _mutagen(rng: random.Random, seeds: Sequence[str]) -> str:
    """
    Build an mXSS candidate inside-out from a trigger, the MutaGen generator (Klein and Johns, IEEE S&P 2024).

    ias-tubs/HTML_parsing_differentials carries no license, so this re-implements mutagen/lib/gen.ml from its structure:
    up to 25 weighted actions wrap the trigger, an action family's odds decay with use (``decr``), and a result of 7 or
    fewer steps, or mostly uninteresting ones, is drawn again. Vendored seed payloads join the three MutaGen triggers.
    """
    while True:
        counts: Counter[str] = Counter()
        markup = rng.choice((*_TRIGGERS, rng.choice(seeds)))
        steps = interesting = 0
        while (
            steps < 25 and (action := _weighted(rng, [(name, weight(counts)) for name, weight in _ACTIONS])) != "stop"
        ):
            counts[action] += 1
            markup = _apply(rng, action, markup)
            interesting += action in {"open_tag", "enclose_tag", "enclose_tag_attribute"}
            steps += 1
        if steps >= 7 and interesting * 3 >= steps:
            return markup


_TRIGGERS: Final = ("<img src=x onerror=mxss(1)>", "<image src=x onerror=mxss(1)>", "<script>mxss(1)</script>")
# gen.ml Action.gen, with its comment, CDATA and URI-encoding variants folded into one family each and their weights
# summed; ``decr p c`` there is p ** (c + 1)
_ACTIONS: Final[tuple[tuple[str, Callable[[Counter[str]], float]], ...]] = (
    ("open_tag", lambda _counts: 1.0),
    ("self_closing_tag", lambda _counts: 1.0),
    ("enclose_tag", lambda _counts: 1.0),
    ("enclose_tag_attribute", lambda _counts: 0.75),
    ("close_tag", lambda counts: max(1.0, 0.1 * counts["open_tag"])),
    ("xml_comment", lambda counts: 5 * 0.125 ** (counts["xml_comment"] + 1)),
    ("js_comment", lambda counts: 0.01 ** (counts["js_comment"] + 1) + 2 * 0.005 ** (counts["js_comment"] + 1)),
    ("uri_encode", lambda counts: 0.0005 ** (counts["uri_encode"] + 1) + 0.0001 ** (counts["uri_encode"] + 1)),
    ("xml_encode", lambda counts: 0.025 ** (counts["xml_encode"] + 1)),
    ("cdata", lambda counts: 3 * 0.05 ** (counts["cdata"] + 1)),
    ("angle_bracket", lambda _counts: 0.2),
    ("parsing_directive", lambda _counts: 0.05),
    ("quote", lambda _counts: 0.25),
    ("space", lambda _counts: 1.0),
    ("stop", lambda _counts: 0.05),
)
_TAGS: Final = (
    ("div", 1.0),
    ("span", 1.0),
    ("title", 1.0),
    ("form", 1.0),
    ("dfn", 1.0),
    ("header", 1.0),
    ("p", 0.5),
    ("br", 0.5),
    ("a", 1.0),
    ("style", 1.0),
    ("noscript", 1.0),
    ("table", 0.25),
    ("td", 0.25),
    ("tr", 0.25),
    ("colgroup", 0.25),
    ("svg", 1.0),
    ("foreignobject", 1.0),
    ("desc", 1.0),
    ("path", 1.0),
    ("math", 1.0),
    ("mtext", 0.5),
    ("mglyph", 0.5),
    ("mi", 0.25),
    ("mo", 0.25),
    ("mn", 0.25),
    ("ms", 0.25),
    ("annotation-xml", 0.33),
    ('annotation-xml encoding="text/html"', 0.33),
    ('annotation-xml encoding="application/xhtml+xml"', 0.33),
    ("select", 1.0),
    ("input", 1.0),
    ("option", 1.0),
    ("textarea", 1.0),
    ("keygen", 1.0),
    ("xmp", 1.0),
    ("noembed", 1.0),
    ("listing", 1.0),
    ("li", 0.5),
    ("ul", 0.5),
    ("pre", 1.0),
    ("var", 1.0),
    ("dl", 0.5),
    ("dt", 0.5),
    ("font", 1.0),
    ("plaintext", 1.0),
    ("noframes", 1.0),
    ("iframe", 1.0),
    ("object", 0.5),
    ("embed", 0.5),
    ("frameset", 0.5),
)
_ATTRIBUTE_QUOTES: Final = (("'", 0.4), ("&apos;", 0.05), ('"', 0.4), ("&quot;", 0.05), ("`", 0.05), ("&grave;", 0.05))
_TOPLEVEL_QUOTES: Final = (("'", 0.4), ("&apos;", 0.1), ('"', 0.4), ("&quot;", 0.1))


def _weighted(rng: random.Random, choices: Sequence[tuple[_T, float]]) -> _T:
    return rng.choices([choice for choice, _ in choices], weights=[weight for _, weight in choices])[0]


def _apply(rng: random.Random, action: str, inner: str) -> str:
    """Wrap ``inner`` with one action as gen.ml's ``Operation.print`` renders it, placed before or after at random."""
    tag = _weighted(rng, _TAGS)
    attrs = "".join(
        _attribute(rng, rng.choice(("foo", "bar", "baz", "abc", "xyz")))
        for _ in range(_weighted(rng, ((0, 1.0), (1, 0.5), (2, 0.25))))
    )
    match action:
        case "enclose_tag":
            wrapped = f"<{tag}{attrs}>{inner}</{tag.split(' ', 1)[0]}>"
        case "enclose_tag_attribute":
            wrapped = f"<{tag}{attrs}{_attribute(rng, inner)}{'/' if rng.random() < 1 / 3 else ''}>"
        case "xml_comment":
            wrapped = rng.choice((
                f"<!--{inner}-->",
                f"<!--{inner}--!>",
                f"<!--{inner}",
                f"{inner}<!--",
                f"-->{inner}",
                f"{inner}--!>",
            ))
        case "js_comment":
            wrapped = _weighted(rng, ((f"/*{inner}*/", 0.5), (f"/*{inner}", 0.25), (f"{inner}*/", 0.25)))
        case "uri_encode":
            # encodeURIComponent keeps only the unreserved marks, encodeURI also the reserved set
            wrapped = quote(inner, safe=_weighted(rng, (("-_.!~*'()", 5.0), (";,/?:@&=+$#-_.!~*'()", 1.0))))
        case "xml_encode":
            wrapped = html.escape(inner).replace("&#x27;", "&apos;")
        case "cdata":
            wrapped = rng.choice((f"<![CDATA[{inner}]]>", f"<![CDATA[{inner}", f"{inner}]]>"))
        case _:
            piece = {
                "open_tag": f"<{tag}{attrs}>",
                "self_closing_tag": f"<{tag}{attrs}/>",
                "close_tag": f"</{tag.split(' ', 1)[0]}>",
                "angle_bracket": rng.choice(("<", ">")),
                "parsing_directive": "<!",
                "quote": _weighted(rng, _TOPLEVEL_QUOTES),
            }.get(action, " ")
            wrapped = piece + inner if rng.random() < 0.5 else inner + piece
    return wrapped


def _attribute(rng: random.Random, value: str) -> str:
    """One attribute in gen.ml's ``Attribute`` forms: plain, space-after-equals or slash, then quoted per ``Quoted``."""
    name = rng.choice(("id", "title", "foo", "name", "data-foo"))
    separator = _weighted(rng, ((f" {name}=", 0.9), (f" {name}= ", 0.05), (f"/{name}=", 0.05)))
    left, right = _weighted(rng, _ATTRIBUTE_QUOTES), _weighted(rng, _ATTRIBUTE_QUOTES)
    return separator + _weighted(
        rng,
        (
            (value, 0.5),
            (f"{left}{value}{right}", 0.25),
            (f"{left}{value}", 0.25),
            (f"{value}{left}", 0.25),
            (f"{left}{value}{left}", 1.0),
        ),
    )


def _splice(rng: random.Random, seed: str, tokens: Sequence[str]) -> str:
    """Insert dictionary tokens at random offsets, libFuzzer's dictionary mutation applied to a seed."""
    for _ in range(rng.randint(1, 6)):
        at = rng.randint(0, len(seed))
        seed = seed[:at] + rng.choice(tokens) + seed[at:]
    return seed


def _url_case(rng: random.Random) -> tuple[str, str, str, str, Policy]:
    """
    Draw an element and URL attribute, a value with one obfuscation written as attribute source, and a URL policy.

    The obfuscations are scheme-check bypasses sanitizers shipped: character references without ``;`` and named
    whitespace references (loofah GHSA-5qhf-9phg-95m2, GHSA-8whx-365g-h9vv), code points above U+00A0 (bleach
    GHSA-8rfp-98v4-mmr6), NFKC look-alikes (html-sanitizer CVE-2024-34078), and ``javascript://://`` (Chromium's
    ``ProtocolIsJavaScript`` disagreeing with KURL).
    """
    base = rng.choice(_URL_BASES)
    recipe, obfuscate = rng.choice(_OBFUSCATIONS)
    at = rng.randint(0, colon if (colon := base.find(":")) > 0 else len(base))
    element, attribute = rng.choice(_URL_TARGETS)
    policy = Policy(
        tags=frozenset({"a", "img", "form", "button", "blockquote", "video", "svg"}),
        attributes={"*": _URL_ATTRS},
        url_schemes=frozenset(rng.sample(_SCHEMES, rng.randint(0, 4))),
        allow_relative_urls=rng.random() < 0.5,
        allow_fragment_urls=rng.random() < 0.5,
    )
    return element, attribute, recipe, obfuscate(base, at).replace('"', "&quot;"), policy


_URL_BASES: Final = (
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "vbscript:msgbox(1)",
    "data:text/html,<script>alert(1)</script>",
    "https://example.com/",
    "http://example.com/x?y#z",
    "mailto:a@example.com",
    "x-custom:1",
    "ftp://example.com/",
    "tel:+1",
    "//example.com/",
    "/path",
    "?query",
    "#fragment",
    "javascript://://alert(1)",
    "javascript:/*\n*/alert(1)",
    "c:\\x",
    "\\\\example.com\\share",
    "java",
    "",
)
_URL_TARGETS: Final = (
    ("a", "href"),
    ("img", "src"),
    ("form", "action"),
    ("button", "formaction"),
    ("blockquote", "cite"),
    ("video", "poster"),
    ("svg", "xlink:href"),
    ("svg", "href"),
)


def _insert(text: str, value: str, at: int) -> str:
    return value[:at] + text + value[at:]


_FULLWIDTH: Final = {code: code + 0xFEE0 for code in range(ord("A"), ord("z") + 1) if chr(code).isalpha()}
_OBFUSCATIONS: Final[tuple[tuple[str, Callable[[str, int], str]], ...]] = (
    ("plain", lambda value, _at: value),
    ("leading c0", lambda value, _at: f"\x01 \x1f{value}"),
    ("leading space refs", lambda value, _at: f"&#32;&#x0c;{value}"),
    ("&colon;", lambda value, _at: value.replace(":", "&colon;", 1)),
    ("uppercase", lambda value, _at: value.upper()),
    (
        "letter as ref no ;",
        lambda value, at: value[:at] + "".join(f"&#{ord(char)}" for char in value[at : at + 1]) + value[at + 1 :],
    ),
    (
        "letter as hex ref",
        lambda value, at: value[:at] + "".join(f"&#x{ord(char):x};" for char in value[at : at + 1]) + value[at + 1 :],
    ),
    ("fullwidth", lambda value, at: value[:at] + value[at : at + 3].translate(_FULLWIDTH) + value[at + 3 :]),
    *(
        (recipe, partial(_insert, text))
        for recipe, text in (
            ("raw tab", "\t"),
            ("raw newline", "\n"),
            ("named &Tab;", "&Tab;"),
            ("named &NewLine;", "&NewLine;"),
            ("decimal ref no ;", "&#9"),
            ("hex ref no ;", "&#x0A"),
            ("zero-padded ref", "&#0000009;"),
            ("nul ref", "&#0;"),
            ("c0 control", "\x01"),
            ("del", "\x7f"),
            ("nbsp", "\u00a0"),
            ("zero width space", "\u200b"),
            ("soft hyphen ref", "&shy;"),
            ("line separator", "\u2028"),
        )
    ),
)


def _confirm_with_parse5(findings: list[tuple[_Finding, Policy]]) -> list[tuple[_Finding, Policy]]:
    """
    Re-parse each minimized mutation finding's output with parse5 and record whether it confirms the mutation.

    ``confirmed`` means parse5 also departs from turbohtml's tree, so the mutation is turbohtml's; ``html5lib-only``
    means parse5 rebuilds turbohtml's tree, pointing at a stale html5lib rule. Node or the npm install may be absent on
    a dev machine; the verdict then says so instead of failing the run.
    """
    mutations = [(finding, policy) for finding, policy in findings if "!= html5lib " in finding.signature]
    if (node := shutil.which("node")) is None or not (_NODE_DIR / "node_modules" / "parse5").is_dir():
        verdicts = dict.fromkeys((finding.signature for finding, _ in mutations), "unavailable")
    else:
        trees = [
            sanitize_node(parse_fragment(finding.markup, _CONTEXT, positions=False), policy)
            for finding, policy in mutations
        ]
        dumps = subprocess.run(
            [node, str(_NODE_DIR / "parse5_tree_runner.js")],
            input=json.dumps([tree.inner_html for tree in trees]),
            capture_output=True,
            text=True,
            cwd=_NODE_DIR,
            # the in-process run preloads the ASan runtime, which node must not inherit
            env={key: value for key, value in os.environ.items() if key not in {"LD_PRELOAD", "DYLD_INSERT_LIBRARIES"}},
            check=True,
        ).stdout
        verdicts = {
            finding.signature: "html5lib-only" if dump == "\n".join(_dump_turbohtml(tree.children)) else "confirmed"
            for (finding, _), tree, dump in zip(mutations, trees, json.loads(dumps), strict=True)
        }
    return [
        (dataclasses.replace(finding, parse5=verdicts.get(finding.signature, finding.parse5)), policy)
        for finding, policy in findings
    ]


if __name__ == "__main__":
    raise SystemExit(main())
