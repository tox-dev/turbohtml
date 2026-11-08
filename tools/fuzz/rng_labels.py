"""Corpus labels expose shared errors that differential equality misses."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import lxml.etree

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


def main(argv: Sequence[str] | None = None) -> int:
    # Distinguish findings from invalid corpora for automated runs.
    parser: Final = argparse.ArgumentParser(description="Compare Jing RELAX NG labels with turbohtml and libxml2.")
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
    # Check both engines against labels to retain shared failures.
    for position, case in enumerate(_cases(suite), 1):
        with tempfile.TemporaryDirectory(prefix="rng-labels-") as directory:
            yield from _compare_case(f"{position:03}", case, Path(directory))


def _compare_case(case_id: str, case: lxml.etree._Element, directory: Path) -> Iterator[Verdict]:
    schema_labels: Final = [child for child in case if child.tag in {"correct", "incorrect"}]
    if len(schema_labels) != 1:
        message: Final = "A testCase must contain one schema label"
        raise _CorpusError(message)
    expected: Final = schema_labels[0].tag == "correct"
    schema_text: Final = _payload(schema_labels[0])
    # the file names and layout follow Jing's own test preparation (test/prep.xsl), so hrefs resolve as Jing ran them
    schema_path: Final = directory / ("c.rng" if expected else "i.rng")
    schema_path.write_bytes(schema_text)
    _write_resources(case, directory)
    schema_hash: Final = hashlib.sha256(lxml.etree.tostring(case)).hexdigest()
    try:
        ours: RelaxNG | None = RelaxNG(schema_text.decode(), base_url=str(schema_path), include_root=directory)
    except (ValueError, OSError):
        ours = None
    try:
        reference: lxml.etree.RelaxNG | None = lxml.etree.RelaxNG(lxml.etree.parse(str(schema_path), _parser()))
    except lxml.etree.RelaxNGParseError:
        reference = None
    yield Verdict(
        case_id=case_id,
        sha256=schema_hash,
        engine="turbohtml",
        phase="compilation",
        expected=expected,
        actual=ours is not None,
    )
    yield Verdict(
        case_id=case_id,
        sha256=schema_hash,
        engine="libxml2",
        phase="compilation",
        expected=expected,
        actual=reference is not None,
    )
    if not expected:
        return
    documents: Final = [child for child in case if child.tag in {"valid", "invalid"}]
    for document_position, document in enumerate(documents, 1):
        document_text = _payload(document)
        document_hash = hashlib.sha256(schema_text + b"\0" + document_text).hexdigest()
        document_id = f"{case_id}/{document_position}"
        valid = document.tag == "valid"
        yield Verdict(
            case_id=document_id,
            sha256=document_hash,
            engine="turbohtml",
            phase="validation",
            expected=valid,
            actual=None if ours is None else ours.validate(parse_xml(document_text.decode())).valid,
        )
        yield Verdict(
            case_id=document_id,
            sha256=document_hash,
            engine="libxml2",
            phase="validation",
            expected=valid,
            actual=None if reference is None else reference.validate(lxml.etree.fromstring(document_text, _parser())),
        )


def _cases(suite: lxml.etree._Element) -> Iterator[lxml.etree._Element]:
    for child in suite:
        if child.tag == "testCase":
            yield child
        elif child.tag == "testSuite":
            yield from _cases(child)


def _write_resources(container: lxml.etree._Element, directory: Path) -> None:
    for child in container:
        if child.tag == "dir":
            (nested := directory / _resource_name(child)).mkdir()
            _write_resources(child, nested)
        elif child.tag == "resource":
            path = directory / _resource_name(child)
            if any(isinstance(element.tag, str) for element in child):
                path.write_bytes(_payload(child))
            else:
                path.write_text(child.text or "", encoding="utf-8")


def _resource_name(element: lxml.etree._Element) -> str:
    name: Final = element.get("name", "")
    if not name or Path(name).name != name or name in {".", ".."}:
        message: Final = "A resource or dir name must be one path component"
        raise _CorpusError(message)
    return name


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


class _CorpusError(ValueError):
    # Separate extraction limits from engine failures.
    ...


@dataclass(frozen=True, kw_only=True, slots=True)
class Verdict:
    # Retain engine-specific evidence without exposing XML content.
    case_id: str
    sha256: str
    engine: str
    phase: str
    expected: bool | None
    actual: bool | None


__all__ = []


if __name__ == "__main__":
    sys.exit(main())
