"""Seed subtrees retain recipient declarations after reference repair."""

from __future__ import annotations

import copy
import random
import re
from typing import Final

import lxml.etree

_XSL: Final[str] = "{http://www.w3.org/1999/XSL/Transform}"
_XS: Final[str] = "{http://www.w3.org/2001/XMLSchema}"
_RNG: Final[str] = "{http://relaxng.org/ns/structure/1.0}"
_PREFIXES: Final[dict[str, str]] = {"x": _XSL[1:-1], "xs": _XS[1:-1], "rng": "http://relaxng.org/ns/structure/1.0"}


def mutate(source: str, pool: tuple[str, ...], seed: int, *, broken: bool) -> str:
    # Repair donor references against declarations in the recipient tree.
    root: Final = _parse(source)
    donors: Final = tuple(_parse(source) for source in pool)
    donor: Final = random.Random(seed).choice(
        tuple(candidate for candidate in donors if candidate.tag == root.tag and _method(candidate) == _method(root))
    )
    if root.tag == f"{_XSL}stylesheet":
        _stylesheet(root, donor, broken=broken)
    elif root.tag == f"{_XS}schema":
        _schema(root, donor, broken=broken)
    elif root.tag == f"{_RNG}grammar":
        _rng(root, donor, broken=broken)
    else:
        message = "only inline XSLT, XSD and RELAX NG seed trees are supported"
        raise ValueError(message)
    bound: Final = lxml.etree.Element(str(root.tag), dict(root.attrib), nsmap={**root.nsmap, **_PREFIXES})
    bound[:] = list(root)
    return lxml.etree.tostring(bound, encoding="unicode")


def _parse(source: str) -> lxml.etree._Element:
    wrapper: Final = lxml.etree.fromstring(
        (
            f'<seed xmlns:x="http://www.w3.org/1999/XSL/Transform" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:rng="http://relaxng.org/ns/structure/1.0">{source}</seed>'
        ).encode(),
        lxml.etree.XMLParser(resolve_entities=False, no_network=True),
    )
    if len(wrapper) != 1:
        message = "seed input requires one root element"
        raise ValueError(message)
    return wrapper[0]


def _method(root: lxml.etree._Element) -> str:
    output: Final = root.find(f"{_XSL}output")
    return "xml" if output is None else output.get("method", "xml")


def _stylesheet(root: lxml.etree._Element, donor: lxml.etree._Element, *, broken: bool) -> None:
    target: Final = root.find(f"{_XSL}template[@match='/']")
    replacement: Final = donor.find(f"{_XSL}template[@match='/']")
    if target is None or replacement is None:
        message = "stylesheet seeds require a root template"
        raise ValueError(message)
    target[:] = [copy.deepcopy(child) for child in replacement]
    variables: Final = [
        name for node in root if node.tag in {f"{_XSL}variable", f"{_XSL}param"} and (name := node.get("name"))
    ]
    templates: Final = [name for node in root if node.tag == f"{_XSL}template" and (name := node.get("name"))]
    keys: Final = [name for node in root if node.tag == f"{_XSL}key" and (name := node.get("name"))]
    for node in target.iter():
        if node.tag == f"{_XSL}call-template" and templates:
            node.set("name", templates[0])
        for attribute in ("select", "test"):
            if (expression := node.get(attribute)) is not None:
                expression = _STYLESHEET_PATTERN.sub(lambda match: _reference(match, variables, keys), expression)
                node.set(attribute, expression)
    if broken:
        lxml.etree.SubElement(target, f"{_XSL}call-template", {"name": "missing"})


def _reference(match: re.Match[str], variables: list[str], keys: list[str]) -> str:
    if match.group("variable") is not None and variables:
        return f"${variables[0]}"
    if match.group("key") is not None and keys:
        return match.group("key") + match.group("quote") + keys[0] + match.group("quote")
    return match.group()


def _schema(root: lxml.etree._Element, donor: lxml.etree._Element, *, broken: bool) -> None:
    target: Final = root.find(f"{_XS}element")
    replacement: Final = donor.find(f"{_XS}element")
    if target is None or replacement is None:
        message = "schema seeds require a global element"
        raise ValueError(message)
    root.remove(target)
    inserted: Final = copy.deepcopy(replacement)
    inserted.set("name", target.get("name", "v"))
    root.append(inserted)
    types: Final = [
        name for node in root if node.tag in {f"{_XS}simpleType", f"{_XS}complexType"} and (name := node.get("name"))
    ]
    for node in inserted.iter():
        for attribute in ("type", "base"):
            if (datatype := node.get(attribute)) is not None and not datatype.startswith("xs:"):
                node.set(attribute, types[0] if types else "xs:string")
    if broken:
        inserted.set("type", "missing")


def _rng(root: lxml.etree._Element, donor: lxml.etree._Element, *, broken: bool) -> None:
    target: Final = root.find(f"{_RNG}start")
    replacement: Final = donor.find(f"{_RNG}start")
    if target is None or replacement is None:
        message = "RELAX NG seeds require a start pattern"
        raise ValueError(message)
    target[:] = [copy.deepcopy(child) for child in replacement]
    definitions: Final = [name for node in root if node.tag == f"{_RNG}define" and (name := node.get("name"))]
    for node in target.iter(f"{_RNG}ref"):
        if definitions:
            node.set("name", definitions[0])
    if broken:
        target[:] = [lxml.etree.Element(f"{_RNG}ref", {"name": "missing"})]


_STYLESHEET_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""(?P<key>(?#key function call)\bkey\s*\(\s*)(?P<quote>(?#opening quote)['"])"""
    r"""(?#quoted key name)[^'"]*(?P=quote)"""
    r"""|(?P<literal>(?#quoted literal)'[^']*'|"[^"]*")"""
    r"""|(?P<variable>(?#variable reference)\$[A-Za-z_][\w.-]*)"""
)


__all__: Final[list[str]] = [
    "mutate",
]
