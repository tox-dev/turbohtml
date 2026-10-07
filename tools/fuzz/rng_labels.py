"""Corpus labels expose shared errors that differential equality misses."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Final

import lxml.etree

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

__all__ = ["Verdict", "compare", "main"]

_RNG: Final = "http://relaxng.org/ns/structure/1.0"
_XML_BASE: Final = "{http://www.w3.org/XML/1998/namespace}base"


def main(argv: Sequence[str] | None = None) -> int:
    """Distinguish findings from invalid corpora for automated runs."""
    parser: Final = argparse.ArgumentParser(
        description="Compare inline Jing RELAX NG labels with turbohtml and libxml2."
    )
    parser.add_argument("--corpus", required=True)
    arguments: Final = parser.parse_args(argv)
    try:
        counts: Final = _report(arguments.corpus)
    except (OSError, _CorpusError, lxml.etree.XMLSyntaxError):
        print("Corpus preparation failed", file=sys.stderr)
        return 2
    print(json.dumps({"summary": dict(counts)}, sort_keys=True))
    return 1 if counts["findings"] else 0 if counts["compilation"] else 2


def _report(corpus: str) -> Counter[str]:
    counts: Final[Counter[str]] = Counter()
    for verdict in compare(lxml.etree.parse(corpus, _parser()).getroot()):
        category = verdict.phase
        if verdict.actual is not None and verdict.actual != verdict.expected:
            category = "findings"
        elif verdict.phase == "validation" and verdict.actual is None:
            category = "unavailable"
        counts[category] += 1
        print(json.dumps(asdict(verdict), sort_keys=True))
    return counts


def compare(suite: lxml.etree._Element) -> Iterator[Verdict]:
    """Check both engines against labels to retain shared failures."""
    for position, case in enumerate(_cases(suite), 1):
        case_id: Final = f"{position:03}"
        if _requires_resources(case):
            yield Verdict(
                case_id,
                hashlib.sha256(lxml.etree.tostring(case)).hexdigest(),
                "both",
                "unsupported-inventory",
                None,
                None,
            )
            continue
        schema_labels: Final = [child for child in case if child.tag in {"correct", "incorrect"}]
        if len(schema_labels) != 1:
            message: Final = "A testCase must contain one schema label"
            raise _CorpusError(message)
        schema_text: Final = _payload(schema_labels[0])
        schema_hash: Final = hashlib.sha256(schema_text).hexdigest()
        expected: Final = schema_labels[0].tag == "correct"
        ours: Final = _compile_turbohtml(schema_text)
        reference: Final = _compile_lxml(schema_text)
        yield Verdict(case_id, schema_hash, "turbohtml", "compilation", expected, ours is not None)
        yield Verdict(case_id, schema_hash, "libxml2", "compilation", expected, reference is not None)
        if not expected:
            continue
        documents: Final = [child for child in case if child.tag in {"valid", "invalid"}]
        for document_position, document in enumerate(documents, 1):
            document_text: Final = _payload(document)
            document_hash: Final = hashlib.sha256(schema_text + b"\0" + document_text).hexdigest()
            document_id: Final = f"{case_id}/{document_position}"
            valid: Final = document.tag == "valid"
            yield Verdict(
                document_id,
                document_hash,
                "turbohtml",
                "validation",
                valid,
                None if ours is None else ours.validate(parse_xml(document_text.decode())).valid,
            )
            yield Verdict(
                document_id,
                document_hash,
                "libxml2",
                "validation",
                valid,
                None if reference is None else reference.validate(lxml.etree.fromstring(document_text, _parser())),
            )


def _cases(suite: lxml.etree._Element) -> Iterator[lxml.etree._Element]:
    for child in suite:
        if child.tag == "testCase":
            yield child
        elif child.tag == "testSuite":
            yield from _cases(child)


def _requires_resources(case: lxml.etree._Element) -> bool:
    return any(child.tag in {"resource", "dir"} for child in case) or any(
        (element.tag in {f"{{{_RNG}}}externalRef", f"{{{_RNG}}}include"} and "href" in element.attrib)
        or _XML_BASE in element.attrib
        for label in case
        if label.tag in {"correct", "incorrect"}
        for element in label.iter()
    )


def _payload(label: lxml.etree._Element) -> bytes:
    elements: Final = [child for child in label if isinstance(child.tag, str)]
    if len(elements) != 1 or "dtd" in label.attrib or (label.text or "").strip():
        message: Final = "Only single-root payloads without DTD declarations are supported"
        raise _CorpusError(message)
    if any((child.tail or "").strip() for child in label):
        tail_error: Final = "Payload text outside the root element is unsupported"
        raise _CorpusError(tail_error)
    return lxml.etree.tostring(elements[0], encoding="utf-8", with_tail=False)


def _parser() -> lxml.etree.XMLParser:
    return lxml.etree.XMLParser(resolve_entities="internal", no_network=True, load_dtd=False)


def _compile_turbohtml(schema: bytes) -> RelaxNG | None:
    try:
        return RelaxNG(schema.decode())
    except ValueError:
        return None


def _compile_lxml(schema: bytes) -> lxml.etree.RelaxNG | None:
    try:
        return lxml.etree.RelaxNG(lxml.etree.fromstring(schema, _parser()))
    except lxml.etree.RelaxNGParseError:
        return None


class _CorpusError(ValueError):
    """Separate extraction limits from engine failures."""


@dataclass(frozen=True)
class Verdict:
    """Retain engine-specific evidence without exposing XML content."""

    case_id: str
    sha256: str
    engine: str
    phase: str
    expected: bool | None
    actual: bool | None


if __name__ == "__main__":
    sys.exit(main())
