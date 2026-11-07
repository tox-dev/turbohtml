"""Keep encoded corpus bytes separate from codec and expectation metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypedDict

from fuzz.structure_generators import (
    BudgetError,
    Generated,
    GenerationBudget,
    Grammar,
    GrammarError,
    Identifier,
    Production,
    ProductionFloorError,
    Reference,
    compile_grammar,
    generate,
    generation_sweep,
    write_corpus,
)
from fuzz.xml_structure_generators import XmlRecord, xml_snapshot

from turbohtml import parse
from turbohtml.detect import EncodingMatch, detect

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from turbohtml import Document


def main(argv: Sequence[str] | None = None) -> int:
    # Write materialized byte files so fuzz consumers need no carrier prefix.
    parser: Final = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=32)
    parser.add_argument("--steps", type=int, default=128)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    args: Final = parser.parse_args(argv)
    if args.count < 0:
        parser.error("count must be nonnegative")
    try:
        cases: Final = (
            tuple(
                encoding_generate(random.Random(args.seed + index), args.budget, steps=args.steps)
                for index in range(args.count)
            )
            if args.count
            else generation_sweep(_GRAMMAR, budget=GenerationBudget(nodes=args.budget, steps=args.steps))
        )
    except (BudgetError, GrammarError, ProductionFloorError) as error:
        parser.error(str(error))
    write_corpus(cases, args.output)
    (args.output / "manifest.json").write_text(
        f"{json.dumps([encoding_manifest(case) for case in cases], indent=2)}\n", encoding="utf-8"
    )
    return 0


def encoding_generate(rng: random.Random, budget: int = 32, *, steps: int = 128) -> Generated:
    # Charge DOM nodes before converting the resulting markup to profile bytes.
    return generate(_GRAMMAR, rng, GenerationBudget(nodes=budget, steps=steps))


def encoding_grammar() -> Grammar:
    # Retain encoding origins and forceable paths through the shared compiler.
    return _GRAMMAR


def encoding_profile(case: Generated) -> EncodingProfile:
    # Keep the codec outside payload bytes, including ambiguous legacy streams.
    return _PROFILES[case.productions[0]]


def encoding_manifest(case: Generated) -> EncodingSeed:
    # Preserve contexts when distinct codecs share one byte hash.
    profile: Final = encoding_profile(case)
    return {
        "path": hashlib.sha256(case.data).hexdigest(),
        "encoding": profile.encoding,
        "codec": profile.codec,
        "declared": profile.declared,
        "sniff": profile.sniff,
        "bom": profile.bom.hex(),
        "decoded": encoding_decoded(case),
        "productions": list(case.productions),
        "origins": [_ORIGINS[name] for name in case.productions],
        "bindings": dict(case.bindings),
        "nodes": case.nodes,
        "depth": case.depth,
    }


def encoding_decoded(case: Generated) -> str:
    # Rebuild literals without asking the native decoder for its expectation.
    profile: Final = encoding_profile(case)
    if profile.encoding == "replacement":
        return "" if profile.empty else "\ufffd"
    prefix: Final = "\ufeff" if profile.bom and profile.encoding in {"UTF-16BE", "UTF-16LE"} else ""
    return (
        prefix + (f'<meta charset="{profile.declared}">' if profile.declared is not None else "") + _markup(_body(case))
    )


def _markup(shape: _Shape) -> str:
    if shape.kind == "text":
        return shape.data
    attrs: Final = "".join(f' {name}="{value}"' for name, value in shape.attrs)
    return f"<{shape.name}{attrs}>{''.join(_markup(child) for child in shape.children)}</{shape.name}>"


def _body(case: Generated) -> _Shape:
    profile: Final = encoding_profile(case)
    anchor: Final = dict(case.bindings)["anchor"]
    return _Shape(
        kind="element",
        name="div",
        attrs=(("id", anchor),),
        children=(
            _Shape(
                kind="element",
                name="p",
                children=(_Shape(kind="text", data=chr(_SAMPLES[profile.encoding.removesuffix("-SIG")][1])),),
            ),
            *_content(iter(case.productions[1:]), profile.encoding.removesuffix("-SIG")),
            _Shape(
                kind="element",
                name="a",
                attrs=(("href", f"#{anchor}"),),
                children=(_Shape(kind="text", data="x"),),
            ),
        ),
    )


def _content(trace: Iterator[str], encoding: str) -> tuple[_Shape, ...]:
    mode: Final = next(trace).rsplit(":", 1)[-1]
    if mode == "empty":
        return ()
    if mode in {"sample", "ascii"}:
        return (_Shape(kind="text", data=chr(_SAMPLES[encoding][1]) if mode == "sample" else "ordinary"),)
    first: Final = _Shape(kind="element", name="span", children=_content(trace, encoding))
    return (
        (first,)
        if mode == "nested"
        else (first, _Shape(kind="element", name="span", children=_content(trace, encoding)))
    )


def encoding_controls() -> dict[str, bool]:
    # Make silent decode or parser loss fail before generating corpus files.
    case: Final = generate(_GRAMMAR, random.Random(0), 16, force="encoding:codec:utf-8")
    sniffed: Final = generate(_GRAMMAR, random.Random(0), 16, force="encoding:meta:utf-8")
    return {
        "missing decoded text": encoding_check(case, decode=lambda _data, _codec: "") is not None,
        "changed parsed tree": encoding_check(case, read=lambda _data, _profile: parse("<p>wrong</p>")) is not None,
        "wrong sniffed codec": encoding_check(
            sniffed, detect_bytes=lambda _data: EncodingMatch(encoding=None, confidence=0.0, language=None)
        )
        is not None,
    }


def encoding_check(
    case: Generated,
    *,
    decode: Callable[[bytes, str], str] = bytes.decode,
    read: Callable[[bytes, EncodingProfile], Document] | None = None,
    detect_bytes: Callable[[bytes], EncodingMatch] = detect,
) -> str | None:
    # Compare the first decode and parse with independent production literals.
    profile: Final = encoding_profile(case)
    if profile.sniff:
        match: Final = detect_bytes(case.data)
        if (match.encoding, match.bom, match.codec) != (profile.encoding, bool(profile.bom), profile.codec):
            return "sniffed codec differs from profile"
    if decode(case.data, profile.codec) != encoding_decoded(case):
        return "decoded bytes differ from production literals"
    if xml_snapshot((read or _read)(case.data, profile)) != encoding_expected(case):
        return "parsed bytes differ from production literals"
    return None


def _read(data: bytes, profile: EncodingProfile) -> Document:
    return parse(data, detect_encoding=True) if profile.sniff else parse(data, encoding=profile.encoding)


def encoding_expected(case: Generated) -> tuple[XmlRecord, ...]:
    # Require literal names, attributes and parents before accepting decoded HTML.
    profile: Final = encoding_profile(case)
    records: Final[list[XmlRecord]] = []
    meta: Final = (
        (_Shape(kind="element", name="meta", attrs=(("charset", profile.declared),)),)
        if profile.declared is not None
        else ()
    )
    body: Final = (
        ()
        if profile.empty
        else (_Shape(kind="text", data="\ufffd"),)
        if profile.encoding == "replacement"
        else (_body(case),)
    )
    _record(
        _Shape(
            kind="document",
            children=(
                _Shape(
                    kind="element",
                    name="html",
                    children=(
                        _Shape(kind="element", name="head", children=meta),
                        _Shape(kind="element", name="body", children=body),
                    ),
                ),
            ),
        ),
        -1,
        records,
    )
    return tuple(records)


def _record(shape: _Shape, parent: int, records: list[XmlRecord]) -> None:
    position: Final = len(records)
    records.append((
        "element:html" if shape.kind == "element" else shape.kind,
        shape.name,
        shape.data,
        shape.attrs,
        parent,
    ))
    for child in shape.children:
        _record(child, position, records)


def _materialize(case: Generated) -> Generated:
    profile: Final = encoding_profile(case)
    return replace(case, data=profile.bom + _encode(case.data, profile.encoding.removesuffix("-SIG")))


def _encode(data: bytes, encoding: str) -> bytes:
    if encoding in {"UTF-8", "replacement"}:
        return data
    if encoding in {"UTF-16BE", "UTF-16LE"}:
        return data.decode("utf-8").encode("utf-16-be" if encoding == "UTF-16BE" else "utf-16-le")
    sample, point, _origin = _SAMPLES[encoding]
    return sample.join(part.encode("ascii") for part in data.decode("utf-8").split(chr(point)))


def _profiles() -> dict[str, EncodingProfile]:
    profiles: Final = {
        f"encoding:codec:{name.lower()}": EncodingProfile(
            name=f"encoding:codec:{name.lower()}", encoding=name, origin=origin
        )
        for name, (_data, _point, origin) in _SAMPLES.items()
    }
    profiles["encoding:codec:replacement:empty"] = EncodingProfile(
        name="encoding:codec:replacement:empty",
        encoding="replacement",
        origin=f"{_SPEC}#replacement-decoder",
        empty=True,
    )
    labels: tuple[tuple[str, str, str], ...] = (
        (
            "unicode-1-1-utf-8",
            "UTF-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L6",
        ),
        (
            "unicode11utf8",
            "UTF-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L7",
        ),
        (
            "unicode20utf8",
            "UTF-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L8",
        ),
        (
            "utf-8",
            "UTF-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L9",
        ),
        (
            "utf8",
            "UTF-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L10",
        ),
        (
            "x-unicode20utf8",
            "UTF-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L11",
        ),
        (
            "866",
            "IBM866",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L22",
        ),
        (
            "cp866",
            "IBM866",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L23",
        ),
        (
            "csibm866",
            "IBM866",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L24",
        ),
        (
            "ibm866",
            "IBM866",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L25",
        ),
        (
            "csisolatin2",
            "ISO-8859-2",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L31",
        ),
        (
            "iso-8859-2",
            "ISO-8859-2",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L32",
        ),
        (
            "iso-ir-101",
            "ISO-8859-2",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L33",
        ),
        (
            "iso8859-2",
            "ISO-8859-2",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L34",
        ),
        (
            "iso88592",
            "ISO-8859-2",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L35",
        ),
        (
            "iso_8859-2",
            "ISO-8859-2",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L36",
        ),
        (
            "iso_8859-2:1987",
            "ISO-8859-2",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L37",
        ),
        (
            "l2",
            "ISO-8859-2",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L38",
        ),
        (
            "latin2",
            "ISO-8859-2",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L39",
        ),
        (
            "csisolatin3",
            "ISO-8859-3",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L45",
        ),
        (
            "iso-8859-3",
            "ISO-8859-3",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L46",
        ),
        (
            "iso-ir-109",
            "ISO-8859-3",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L47",
        ),
        (
            "iso8859-3",
            "ISO-8859-3",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L48",
        ),
        (
            "iso88593",
            "ISO-8859-3",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L49",
        ),
        (
            "iso_8859-3",
            "ISO-8859-3",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L50",
        ),
        (
            "iso_8859-3:1988",
            "ISO-8859-3",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L51",
        ),
        (
            "l3",
            "ISO-8859-3",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L52",
        ),
        (
            "latin3",
            "ISO-8859-3",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L53",
        ),
        (
            "csisolatin4",
            "ISO-8859-4",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L59",
        ),
        (
            "iso-8859-4",
            "ISO-8859-4",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L60",
        ),
        (
            "iso-ir-110",
            "ISO-8859-4",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L61",
        ),
        (
            "iso8859-4",
            "ISO-8859-4",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L62",
        ),
        (
            "iso88594",
            "ISO-8859-4",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L63",
        ),
        (
            "iso_8859-4",
            "ISO-8859-4",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L64",
        ),
        (
            "iso_8859-4:1988",
            "ISO-8859-4",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L65",
        ),
        (
            "l4",
            "ISO-8859-4",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L66",
        ),
        (
            "latin4",
            "ISO-8859-4",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L67",
        ),
        (
            "csisolatincyrillic",
            "ISO-8859-5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L73",
        ),
        (
            "cyrillic",
            "ISO-8859-5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L74",
        ),
        (
            "iso-8859-5",
            "ISO-8859-5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L75",
        ),
        (
            "iso-ir-144",
            "ISO-8859-5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L76",
        ),
        (
            "iso8859-5",
            "ISO-8859-5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L77",
        ),
        (
            "iso88595",
            "ISO-8859-5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L78",
        ),
        (
            "iso_8859-5",
            "ISO-8859-5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L79",
        ),
        (
            "iso_8859-5:1988",
            "ISO-8859-5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L80",
        ),
        (
            "arabic",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L86",
        ),
        (
            "asmo-708",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L87",
        ),
        (
            "csiso88596e",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L88",
        ),
        (
            "csiso88596i",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L89",
        ),
        (
            "csisolatinarabic",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L90",
        ),
        (
            "ecma-114",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L91",
        ),
        (
            "iso-8859-6",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L92",
        ),
        (
            "iso-8859-6-e",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L93",
        ),
        (
            "iso-8859-6-i",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L94",
        ),
        (
            "iso-ir-127",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L95",
        ),
        (
            "iso8859-6",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L96",
        ),
        (
            "iso88596",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L97",
        ),
        (
            "iso_8859-6",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L98",
        ),
        (
            "iso_8859-6:1987",
            "ISO-8859-6",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L99",
        ),
        (
            "csisolatingreek",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L105",
        ),
        (
            "ecma-118",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L106",
        ),
        (
            "elot_928",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L107",
        ),
        (
            "greek",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L108",
        ),
        (
            "greek8",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L109",
        ),
        (
            "iso-8859-7",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L110",
        ),
        (
            "iso-ir-126",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L111",
        ),
        (
            "iso8859-7",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L112",
        ),
        (
            "iso88597",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L113",
        ),
        (
            "iso_8859-7",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L114",
        ),
        (
            "iso_8859-7:1987",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L115",
        ),
        (
            "sun_eu_greek",
            "ISO-8859-7",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L116",
        ),
        (
            "csiso88598e",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L122",
        ),
        (
            "csisolatinhebrew",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L123",
        ),
        (
            "hebrew",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L124",
        ),
        (
            "iso-8859-8",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L125",
        ),
        (
            "iso-8859-8-e",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L126",
        ),
        (
            "iso-ir-138",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L127",
        ),
        (
            "iso8859-8",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L128",
        ),
        (
            "iso88598",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L129",
        ),
        (
            "iso_8859-8",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L130",
        ),
        (
            "iso_8859-8:1988",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L131",
        ),
        (
            "visual",
            "ISO-8859-8",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L132",
        ),
        (
            "csiso88598i",
            "ISO-8859-8-I",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L138",
        ),
        (
            "iso-8859-8-i",
            "ISO-8859-8-I",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L139",
        ),
        (
            "logical",
            "ISO-8859-8-I",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L140",
        ),
        (
            "csisolatin6",
            "ISO-8859-10",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L146",
        ),
        (
            "iso-8859-10",
            "ISO-8859-10",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L147",
        ),
        (
            "iso-ir-157",
            "ISO-8859-10",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L148",
        ),
        (
            "iso8859-10",
            "ISO-8859-10",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L149",
        ),
        (
            "iso885910",
            "ISO-8859-10",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L150",
        ),
        (
            "l6",
            "ISO-8859-10",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L151",
        ),
        (
            "latin6",
            "ISO-8859-10",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L152",
        ),
        (
            "iso-8859-13",
            "ISO-8859-13",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L158",
        ),
        (
            "iso8859-13",
            "ISO-8859-13",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L159",
        ),
        (
            "iso885913",
            "ISO-8859-13",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L160",
        ),
        (
            "iso-8859-14",
            "ISO-8859-14",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L166",
        ),
        (
            "iso8859-14",
            "ISO-8859-14",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L167",
        ),
        (
            "iso885914",
            "ISO-8859-14",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L168",
        ),
        (
            "csisolatin9",
            "ISO-8859-15",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L174",
        ),
        (
            "iso-8859-15",
            "ISO-8859-15",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L175",
        ),
        (
            "iso8859-15",
            "ISO-8859-15",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L176",
        ),
        (
            "iso885915",
            "ISO-8859-15",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L177",
        ),
        (
            "iso_8859-15",
            "ISO-8859-15",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L178",
        ),
        (
            "l9",
            "ISO-8859-15",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L179",
        ),
        (
            "iso-8859-16",
            "ISO-8859-16",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L185",
        ),
        (
            "cskoi8r",
            "KOI8-R",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L191",
        ),
        (
            "koi",
            "KOI8-R",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L192",
        ),
        (
            "koi8",
            "KOI8-R",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L193",
        ),
        (
            "koi8-r",
            "KOI8-R",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L194",
        ),
        (
            "koi8_r",
            "KOI8-R",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L195",
        ),
        (
            "koi8-ru",
            "KOI8-U",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L201",
        ),
        (
            "koi8-u",
            "KOI8-U",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L202",
        ),
        (
            "csmacintosh",
            "macintosh",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L208",
        ),
        (
            "mac",
            "macintosh",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L209",
        ),
        (
            "macintosh",
            "macintosh",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L210",
        ),
        (
            "x-mac-roman",
            "macintosh",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L211",
        ),
        (
            "dos-874",
            "windows-874",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L217",
        ),
        (
            "iso-8859-11",
            "windows-874",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L218",
        ),
        (
            "iso8859-11",
            "windows-874",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L219",
        ),
        (
            "iso885911",
            "windows-874",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L220",
        ),
        (
            "tis-620",
            "windows-874",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L221",
        ),
        (
            "windows-874",
            "windows-874",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L222",
        ),
        (
            "cp1250",
            "windows-1250",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L228",
        ),
        (
            "windows-1250",
            "windows-1250",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L229",
        ),
        (
            "x-cp1250",
            "windows-1250",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L230",
        ),
        (
            "cp1251",
            "windows-1251",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L236",
        ),
        (
            "windows-1251",
            "windows-1251",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L237",
        ),
        (
            "x-cp1251",
            "windows-1251",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L238",
        ),
        (
            "ansi_x3.4-1968",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L244",
        ),
        (
            "ascii",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L245",
        ),
        (
            "cp1252",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L246",
        ),
        (
            "cp819",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L247",
        ),
        (
            "csisolatin1",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L248",
        ),
        (
            "ibm819",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L249",
        ),
        (
            "iso-8859-1",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L250",
        ),
        (
            "iso-ir-100",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L251",
        ),
        (
            "iso8859-1",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L252",
        ),
        (
            "iso88591",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L253",
        ),
        (
            "iso_8859-1",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L254",
        ),
        (
            "iso_8859-1:1987",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L255",
        ),
        (
            "l1",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L256",
        ),
        (
            "latin1",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L257",
        ),
        (
            "us-ascii",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L258",
        ),
        (
            "windows-1252",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L259",
        ),
        (
            "x-cp1252",
            "windows-1252",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L260",
        ),
        (
            "cp1253",
            "windows-1253",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L266",
        ),
        (
            "windows-1253",
            "windows-1253",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L267",
        ),
        (
            "x-cp1253",
            "windows-1253",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L268",
        ),
        (
            "cp1254",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L274",
        ),
        (
            "csisolatin5",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L275",
        ),
        (
            "iso-8859-9",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L276",
        ),
        (
            "iso-ir-148",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L277",
        ),
        (
            "iso8859-9",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L278",
        ),
        (
            "iso88599",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L279",
        ),
        (
            "iso_8859-9",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L280",
        ),
        (
            "iso_8859-9:1989",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L281",
        ),
        (
            "l5",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L282",
        ),
        (
            "latin5",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L283",
        ),
        (
            "windows-1254",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L284",
        ),
        (
            "x-cp1254",
            "windows-1254",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L285",
        ),
        (
            "cp1255",
            "windows-1255",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L291",
        ),
        (
            "windows-1255",
            "windows-1255",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L292",
        ),
        (
            "x-cp1255",
            "windows-1255",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L293",
        ),
        (
            "cp1256",
            "windows-1256",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L299",
        ),
        (
            "windows-1256",
            "windows-1256",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L300",
        ),
        (
            "x-cp1256",
            "windows-1256",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L301",
        ),
        (
            "cp1257",
            "windows-1257",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L307",
        ),
        (
            "windows-1257",
            "windows-1257",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L308",
        ),
        (
            "x-cp1257",
            "windows-1257",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L309",
        ),
        (
            "cp1258",
            "windows-1258",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L315",
        ),
        (
            "windows-1258",
            "windows-1258",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L316",
        ),
        (
            "x-cp1258",
            "windows-1258",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L317",
        ),
        (
            "x-mac-cyrillic",
            "x-mac-cyrillic",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L323",
        ),
        (
            "x-mac-ukrainian",
            "x-mac-cyrillic",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L324",
        ),
        (
            "chinese",
            "GBK",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L335",
        ),
        (
            "csgb2312",
            "GBK",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L336",
        ),
        (
            "csiso58gb231280",
            "GBK",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L337",
        ),
        (
            "gb2312",
            "GBK",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L338",
        ),
        (
            "gb_2312",
            "GBK",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L339",
        ),
        (
            "gb_2312-80",
            "GBK",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L340",
        ),
        (
            "gbk",
            "GBK",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L341",
        ),
        (
            "iso-ir-58",
            "GBK",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L342",
        ),
        (
            "x-gbk",
            "GBK",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L343",
        ),
        (
            "gb18030",
            "gb18030",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L349",
        ),
        (
            "big5",
            "Big5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L360",
        ),
        (
            "big5-hkscs",
            "Big5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L361",
        ),
        (
            "cn-big5",
            "Big5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L362",
        ),
        (
            "csbig5",
            "Big5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L363",
        ),
        (
            "x-x-big5",
            "Big5",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L364",
        ),
        (
            "cseucpkdfmtjapanese",
            "EUC-JP",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L375",
        ),
        (
            "euc-jp",
            "EUC-JP",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L376",
        ),
        (
            "x-euc-jp",
            "EUC-JP",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L377",
        ),
        (
            "csiso2022jp",
            "ISO-2022-JP",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L383",
        ),
        (
            "iso-2022-jp",
            "ISO-2022-JP",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L384",
        ),
        (
            "csshiftjis",
            "Shift_JIS",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L390",
        ),
        (
            "ms932",
            "Shift_JIS",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L391",
        ),
        (
            "ms_kanji",
            "Shift_JIS",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L392",
        ),
        (
            "shift-jis",
            "Shift_JIS",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L393",
        ),
        (
            "shift_jis",
            "Shift_JIS",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L394",
        ),
        (
            "sjis",
            "Shift_JIS",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L395",
        ),
        (
            "windows-31j",
            "Shift_JIS",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L396",
        ),
        (
            "x-sjis",
            "Shift_JIS",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L397",
        ),
        (
            "cseuckr",
            "EUC-KR",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L408",
        ),
        (
            "csksc56011987",
            "EUC-KR",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L409",
        ),
        (
            "euc-kr",
            "EUC-KR",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L410",
        ),
        (
            "iso-ir-149",
            "EUC-KR",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L411",
        ),
        (
            "korean",
            "EUC-KR",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L412",
        ),
        (
            "ks_c_5601-1987",
            "EUC-KR",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L413",
        ),
        (
            "ks_c_5601-1989",
            "EUC-KR",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L414",
        ),
        (
            "ksc5601",
            "EUC-KR",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L415",
        ),
        (
            "ksc_5601",
            "EUC-KR",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L416",
        ),
        (
            "windows-949",
            "EUC-KR",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L417",
        ),
        (
            "csiso2022kr",
            "replacement",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L428",
        ),
        (
            "hz-gb-2312",
            "replacement",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L429",
        ),
        (
            "iso-2022-cn",
            "replacement",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L430",
        ),
        (
            "iso-2022-cn-ext",
            "replacement",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L431",
        ),
        (
            "iso-2022-kr",
            "replacement",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L432",
        ),
        (
            "replacement",
            "replacement",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L433",
        ),
        (
            "unicodefffe",
            "UTF-16BE",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L439",
        ),
        (
            "utf-16be",
            "UTF-16BE",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L440",
        ),
        (
            "csunicode",
            "UTF-16LE",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L446",
        ),
        (
            "iso-10646-ucs-2",
            "UTF-16LE",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L447",
        ),
        (
            "ucs-2",
            "UTF-16LE",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L448",
        ),
        (
            "unicode",
            "UTF-16LE",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L449",
        ),
        (
            "unicodefeff",
            "UTF-16LE",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L450",
        ),
        (
            "utf-16",
            "UTF-16LE",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L451",
        ),
        (
            "utf-16le",
            "UTF-16LE",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L452",
        ),
        (
            "x-user-defined",
            "x-user-defined",
            "https://github.com/whatwg/encoding/blob/a985b62a9b45c17da3e17a9f0a0b4e30c34c4a8a/encodings.json#L458",
        ),
    )
    for label, name, origin in labels:
        encoding: Final = (
            "UTF-8-SIG"
            if name == "replacement"
            else "UTF-8"
            if name in {"UTF-16BE", "UTF-16LE"}
            else "windows-1252"
            if name == "x-user-defined"
            else name
        )
        profiles[f"encoding:meta:{label}"] = EncodingProfile(
            name=f"encoding:meta:{label}",
            encoding=encoding,
            origin=origin,
            declared=label,
            bom=b"\xef\xbb\xbf" if name == "replacement" else b"",
            sniff=True,
        )
    for bom_encoding, bom in (("UTF-8-SIG", b"\xef\xbb\xbf"), ("UTF-16BE", b"\xfe\xff"), ("UTF-16LE", b"\xff\xfe")):
        profiles[f"encoding:bom:{bom_encoding.lower()}"] = EncodingProfile(
            name=f"encoding:bom:{bom_encoding.lower()}",
            encoding=bom_encoding,
            origin=f"{_SPEC}#bom-sniff",
            bom=bom,
            sniff=True,
        )
    return profiles


def _productions() -> tuple[Production, ...]:
    productions: Final[list[Production]] = []
    for profile in _PROFILES.values():
        if profile.encoding == "replacement":
            productions.append(
                Production(
                    name=profile.name,
                    symbol="encoded",
                    parts=() if profile.empty else (b"ordinary",),
                    nodes=4 if profile.empty else 5,
                    origin=profile.origin,
                    height=2 if profile.empty else 3,
                )
            )
        else:
            productions.append(
                Production(
                    name=profile.name,
                    symbol="encoded",
                    parts=(
                        f'<meta charset="{profile.declared}">'.encode() if profile.declared is not None else b"",
                        b'<div id="',
                        Identifier(name="anchor", definition=True),
                        b'"><p>' + chr(_SAMPLES[profile.encoding.removesuffix("-SIG")][1]).encode() + b"</p>",
                        Reference(symbol="content:" + profile.encoding.removesuffix("-SIG"), depth=4),
                        b'<a href="#',
                        Identifier(name="anchor", definition=False),
                        b'">x</a></div>',
                    ),
                    nodes=9 + (profile.declared is not None),
                    origin=profile.origin,
                    height=5,
                )
            )
    html = "https://html.spec.whatwg.org/multipage/text-level-semantics.html#the-span-element"
    for name, (_data, point, origin) in _SAMPLES.items():
        if name != "replacement":
            symbol: Final = f"content:{name}"
            productions.extend((
                Production(
                    name=f"{symbol}:sample", symbol=symbol, parts=(chr(point).encode(),), nodes=1, origin=origin
                ),
                Production(
                    name=f"{symbol}:ascii", symbol=symbol, parts=(b"ordinary",), nodes=1, origin=f"{_SPEC}#ascii"
                ),
                Production(
                    name=f"{symbol}:empty", symbol=symbol, parts=(), nodes=0, origin=f"{_SPEC}#concept-stream-end"
                ),
                Production(
                    name=f"{symbol}:nested",
                    symbol=symbol,
                    parts=(b"<span>", Reference(symbol=symbol), b"</span>"),
                    nodes=1,
                    origin=html,
                ),
                Production(
                    name=f"{symbol}:siblings",
                    symbol=symbol,
                    parts=(b"<span>", Reference(symbol=symbol), b"</span><span>", Reference(symbol=symbol), b"</span>"),
                    nodes=2,
                    origin=html,
                ),
            ))
    return tuple(productions)


@dataclass(frozen=True, kw_only=True, slots=True)
class EncodingProfile:
    # Retain the declaration separately when HTML remaps its codec.
    name: str
    encoding: str
    origin: str
    declared: str | None = None
    bom: bytes = b""
    sniff: bool = False
    empty: bool = False

    @property
    def codec(self) -> str:
        # Use the WHATWG decoder because CPython aliases can map different text.
        return f"whatwg-{self.encoding.lower()}"


@dataclass(frozen=True, slots=True, kw_only=True)
class _Shape:
    kind: Literal["document", "element", "text"]
    name: str = ""
    data: str = ""
    attrs: tuple[tuple[str, str], ...] = ()
    children: tuple[_Shape, ...] = ()


class EncodingSeed(TypedDict):
    # Keep codec expectations outside byte files that fuzzers mutate.
    path: str
    encoding: str
    codec: str
    declared: str | None
    sniff: bool
    bom: str
    decoded: str
    productions: list[str]
    origins: list[str]
    bindings: dict[str, str]
    nodes: int
    depth: int


_SPEC: Final[str] = "https://encoding.spec.whatwg.org"

_SAMPLES: Final[dict[str, tuple[bytes, int, str]]] = {
    "UTF-8": (b"\xc3\xa9", 233, "https://encoding.spec.whatwg.org/#utf-8-decoder"),
    "IBM866": (b"\x80", 1040, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-2": (b"\xa1", 260, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-3": (b"\xa1", 294, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-4": (b"\xa1", 260, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-5": (b"\xa1", 1025, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-6": (b"\xa4", 164, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-7": (b"\xa1", 8216, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-8": (b"\xa2", 162, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-8-I": (b"\xa2", 162, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-10": (b"\xa1", 260, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-13": (b"\xa1", 8221, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-14": (b"\xa1", 7682, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-15": (b"\xa1", 161, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "ISO-8859-16": (b"\xa1", 260, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "KOI8-R": (b"\x80", 9472, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "KOI8-U": (b"\x80", 9472, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "macintosh": (b"\x80", 196, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "windows-874": (b"\x80", 8364, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "windows-1250": (b"\x80", 8364, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "windows-1251": (b"\x80", 1026, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "windows-1252": (b"\x80", 8364, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "windows-1253": (b"\x80", 8364, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "windows-1254": (b"\x80", 8364, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "windows-1255": (b"\x80", 8364, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "windows-1256": (b"\x80", 8364, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "windows-1257": (b"\x80", 8364, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "windows-1258": (b"\x80", 8364, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "x-mac-cyrillic": (b"\x80", 1040, "https://encoding.spec.whatwg.org/#single-byte-decoder"),
    "GBK": (b"\xd2\xbb", 19968, "https://encoding.spec.whatwg.org/#gbk-decoder"),
    "gb18030": (b"\x810\x810", 128, "https://encoding.spec.whatwg.org/#gb18030-decoder"),
    "Big5": (b"\xa4@", 19968, "https://encoding.spec.whatwg.org/#big5-decoder"),
    "EUC-JP": (b"\xa4\xa2", 12354, "https://encoding.spec.whatwg.org/#euc-jp-decoder"),
    "ISO-2022-JP": (b'\x1b$B$"\x1b(B', 12354, "https://encoding.spec.whatwg.org/#iso-2022-jp-decoder"),
    "Shift_JIS": (b"\x82\xa0", 12354, "https://encoding.spec.whatwg.org/#shift-jis-decoder"),
    "EUC-KR": (b"\xb0\xa1", 44032, "https://encoding.spec.whatwg.org/#euc-kr-decoder"),
    "replacement": (b"ordinary", 65533, "https://encoding.spec.whatwg.org/#replacement-decoder"),
    "UTF-16BE": (b"\x00\xe9", 233, "https://encoding.spec.whatwg.org/#shared-utf-16-decoder"),
    "UTF-16LE": (b"\xe9\x00", 233, "https://encoding.spec.whatwg.org/#shared-utf-16-decoder"),
    "x-user-defined": (b"\x80", 63360, "https://encoding.spec.whatwg.org/#x-user-defined-decoder"),
}

_PROFILES: Final[dict[str, EncodingProfile]] = _profiles()
_PRODUCTIONS: Final[tuple[Production, ...]] = _productions()
_ORIGINS: Final[dict[str, str]] = {production.name: production.origin for production in _PRODUCTIONS}
_GRAMMAR: Final[Grammar] = compile_grammar(_PRODUCTIONS, "encoded", materialize=_materialize)

__all__ = [
    "EncodingProfile",
    "EncodingSeed",
    "encoding_check",
    "encoding_controls",
    "encoding_decoded",
    "encoding_expected",
    "encoding_generate",
    "encoding_grammar",
    "encoding_manifest",
    "encoding_profile",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
