"""Round-trip safety of serialize(layout=Minify(...)) over a borrowed adversarial corpus.

Minification is only correct if the minified bytes reparse to the same tree. This
suite enforces that property at scale against the html5lib-tests tree-construction
suite -- 1.7k adversarial snippets borrowed from the html5lib project and already
vendored for the conformance harness.

The check is idempotence under reparse: ``minify(parse(minify(parse(src))))`` must
equal ``minify(parse(src))``. A tag omission or whitespace fold that changed the
tree would shift the second pass. Pathological adoption-agency inputs are not
idempotent even under the plain serializer (``<a><a>`` reparses to siblings), so
those cases are measured against the plain-serializer baseline and only count when
the plain serializer itself round-trips.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from turbohtml import Html, Minify, parse

_TREE_DIR = Path(__file__).parents[1] / "html5lib-tests" / "tree-construction"
_MINIFY = Minify()
# the CSS pass rewrites every <style> body and style="" value the corpus carries; the
# value-safe engine is itself idempotent, so enabling it must keep the reparse a fixpoint
_MINIFY_CSS = Minify(minify_css=True)


def _iter_dat(path: Path) -> list[str]:
    cases: list[str] = []
    for raw in path.read_text(encoding="utf-8").split("\n#data\n"):
        block = raw.removeprefix("#data\n")
        data, _, rest = block.partition("\n#errors")
        if "#document-fragment\n" in rest or "#script-on" in rest or "\n#document\n" not in rest:
            continue
        cases.append(data)
    return cases


def _plain_roundtrips(source: str) -> bool:
    once = parse(source).serialize()
    return once == parse(once).serialize()


def _minify_idempotent(source: str, layout: Minify) -> bool:
    once = parse(source).serialize(Html(layout=layout))
    return once == parse(once).serialize(Html(layout=layout))


@pytest.mark.parametrize("layout", [pytest.param(_MINIFY, id="default"), pytest.param(_MINIFY_CSS, id="minify-css")])
@pytest.mark.parametrize("filename", sorted(p.name for p in _TREE_DIR.glob("*.dat")))
def test_minify_idempotent_over_tree_construction(filename: str, layout: Minify) -> None:
    # only the subset the plain serializer round-trips can be asked of the minifier;
    # the rest are inherently non-idempotent adoption-agency reconstructions
    failures = [
        f"{data!r}\n  once:    {parse(data).serialize(Html(layout=layout))!r}\n"
        f"  reparse: {parse(parse(data).serialize(Html(layout=layout))).serialize(Html(layout=layout))!r}"
        for data in _iter_dat(_TREE_DIR / filename)
        if _plain_roundtrips(data) and not _minify_idempotent(data, layout)
    ]
    assert not failures, f"{filename}: {len(failures)} non-idempotent\n\n" + "\n\n".join(failures[:5])


def test_minify_idempotent_over_large_document() -> None:
    # a large well-formed document exercises every transform at scale (whitespace,
    # optional tags, attribute unquoting, comment stripping) past the serialization
    # buffer's growth, where the per-snippet suite stays small
    section = (
        "<section id='s{i}'>\n"
        "  <h2>Heading {i} &amp; more</h2>\n"
        "  <p class='lead'>Some   prose with    spaces and a <a href='/x{i}'>link</a> here.</p>\n"
        "  <ul>\n    <li>one</li>\n    <li>two</li>\n  </ul>\n"
        "  <!-- note {i} -->\n"
        "  <table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>\n"
        "</section>\n"
    )
    big = (
        "<!doctype html><html><head><title>Big</title></head><body>\n"
        + "".join(section.format(i=index) for index in range(500))
        + "</body></html>"
    )
    once = parse(big).serialize(Html(layout=_MINIFY))
    assert once == parse(once).serialize(Html(layout=_MINIFY))
    assert len(once) < len(parse(big).serialize())  # minification actually shrinks the document
