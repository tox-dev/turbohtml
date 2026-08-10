"""
Check that the parties a benchmark compares actually answer the same question.

The harness times functions that discard their results, so nothing in it can tell a fast implementation from one that
did less work. That gap hid six defects at once: a competitor whose default parser matched nothing on a page carrying
an ``xmlns`` and timed an empty node set, a tree builder that extracted half the text every other parser did, and a
link filter reading raw attributes against one that resolved and deduplicated them. Each looked like a clean win or
loss in a published table.

This runs each party's real API over each corpus page in that party's own venv and reports what it matched. A count
far from the median for its probe is a party measuring something else. Run it after adding a competitor or changing
what an operation does::

    python -m bench.equivalence

Counts do not have to agree exactly. Libraries legitimately differ on whitespace handling and on whether a template or
a comment counts as an element, so the check flags a party only when it is at zero or off by more than a factor of
four, which is the range that separates "counts comments differently" from "matched nothing".
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

from bench import corpus
from bench import orchestrator as orch

# each probe body calls emit() with the counts for the page it is handed; the selectors are the ones core.py
# benchmarks, so a disagreement here is a disagreement in a published table
PROBES: Final[dict[str, tuple[tuple[str, ...], str]]] = {
    "turbohtml": (
        (),
        """
import turbohtml
doc = turbohtml.parse(TEXT)
emit(
    elements=len([n for n in doc.xpath("//*") if isinstance(n, turbohtml.Element)]),
    anchors=len(doc.find_all("a")),
    css=len(doc.select("div a[href]")),
    text=doc.text,
)
""",
    ),
    "lxml": (
        ("lxml>=5.2", "cssselect>=1.2"),
        """
from lxml import html as lxml_html
doc = lxml_html.document_fromstring(TEXT)
emit(
    elements=len(doc.xpath("//*")),
    anchors=len(doc.xpath("//a")),
    css=len(doc.cssselect("div a[href]")),
    text=doc.text_content(),
)
""",
    ),
    "selectolax": (
        ("selectolax>=0.4.10",),
        """
from selectolax.lexbor import LexborHTMLParser
doc = LexborHTMLParser(TEXT.encode())
emit(elements=len(doc.css("*")), anchors=len(doc.css("a")), css=len(doc.css("div a[href]")), text=doc.text())
""",
    ),
    "JustHTML": (
        ("justhtml>=3.11",),
        """
from justhtml import JustHTML
doc = JustHTML(TEXT, sanitize=False).root
emit(
    elements=len(doc.query("*")),
    anchors=len(doc.query("a")),
    css=len(doc.query("div a[href]")),
    text=doc.to_text(separator="", strip=False),
)
""",
    ),
    "BeautifulSoup (html.parser)": (
        ("beautifulsoup4>=4.15", "soupsieve>=2.5"),
        """
from bs4 import BeautifulSoup
doc = BeautifulSoup(TEXT, "html.parser")
emit(
    elements=len(doc.find_all(True)),
    anchors=len(doc.find_all("a")),
    css=len(doc.select("div a[href]")),
    text=doc.get_text(),
)
""",
    ),
    "BeautifulSoup (lxml)": (
        ("beautifulsoup4>=4.15", "soupsieve>=2.5", "lxml>=5.2"),
        """
from bs4 import BeautifulSoup
doc = BeautifulSoup(TEXT, "lxml")
emit(
    elements=len(doc.find_all(True)),
    anchors=len(doc.find_all("a")),
    css=len(doc.select("div a[href]")),
    text=doc.get_text(),
)
""",
    ),
    "parsel": (
        ("parsel>=1.11", "cssselect>=1.2"),
        """
from parsel import Selector
doc = Selector(text=TEXT)
emit(
    elements=len(doc.xpath("//*")),
    anchors=len(doc.css("a")),
    css=len(doc.css("div a[href]")),
    text="".join(doc.xpath("//text()").getall()),
)
""",
    ),
    "pyquery": (
        ("pyquery>=2.0.1",),
        """
from pyquery import PyQuery
doc = PyQuery(TEXT, parser="html")
emit(elements=len(doc("*")), anchors=len(doc("a")), css=len(doc("div a[href]")), text=doc.text() or "")
""",
    ),
    "resiliparse": (
        ("resiliparse>=0.14",),
        """
from resiliparse.parse.html import HTMLTree
body = HTMLTree.parse(TEXT).body
emit(
    elements=len(body.query_selector_all("*")) if body else 0,
    anchors=len(body.get_elements_by_tag_name("a")) if body else 0,
    css=len(body.query_selector_all("div a[href]")) if body else 0,
    text=body.text if body else "",
)
""",
    ),
}

# text reports twice: raw length and length after collapsing whitespace runs. A party that merely joins nodes without
# separators differs on the first and agrees on the second, which tells a formatting difference apart from content the
# parser never produced.
_PREAMBLE: Final = """\
import json, sys
TEXT = open(sys.argv[1], encoding='utf-8').read()
def emit(*, elements, anchors, css, text):
    print(json.dumps({
        "elements": elements, "anchors": anchors, "css div a[href]": css,
        "text raw": len(text), "text collapsed": len(" ".join(text.split())),
    }))
"""

# a party at zero, or off the median by more than this, is answering a different question rather than differing on
# whether a comment counts as a node
_TOLERANCE: Final = 4.0


def _counts(name: str, requirements: tuple[str, ...], body: str, page: Path, workdir: Path) -> dict[str, int]:
    """Run one party's probe over one page in that party's own venv; an unbuildable party reports nothing."""
    slug = "".join(char if char.isalnum() else "-" for char in name)
    python = (
        orch.baseline_python(workdir, pgo=False)
        if name == "turbohtml"
        else orch.venv_python(workdir, slug, requirements)
    )
    script = workdir / f"probe-{slug}.py"
    script.write_text(_PREAMBLE + body, "utf-8")
    done = subprocess.run([str(python), str(script), str(page)], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        print(f"  {name}: probe failed, {done.stderr.strip().splitlines()[-1:]}", file=sys.stderr)
        return {}
    return json.loads(done.stdout)


def disagreements(workdir: Path) -> list[str]:
    """Run every probe over every corpus page and return one line per party answering a different question."""
    corpus.prefetch()
    found: list[str] = []
    for label, filename, url in corpus.REAL_PAGES:
        page = workdir / filename
        page.write_text(corpus.large_text(filename, url), encoding="utf-8")
        results = {name: _counts(name, reqs, body, page, workdir) for name, (reqs, body) in PROBES.items()}
        width = max(len(name) for name in results)
        probes = sorted({probe for row in results.values() for probe in row})
        print(f"\n===== {label} =====")
        print(f"{'party':{width}}  " + "  ".join(f"{probe:>18}" for probe in probes))
        for name, row in results.items():
            print(f"{name:{width}}  " + "  ".join(f"{row.get(probe, '-')!s:>18}" for probe in probes))
        for probe in probes:
            # raw text length is a formatting difference: a party that joins runs without separators, or collapses
            # whitespace as pyquery does, reports a different length for identical content. The collapsed length is
            # what says whether the content itself is there.
            if probe == "text raw":
                continue
            counts = [row[probe] for row in results.values() if probe in row]
            middle = statistics.median(counts) if len(counts) > 1 else 0
            if middle <= 0:
                continue
            for name, row in results.items():
                value = row.get(probe)
                if value is None:
                    continue
                if value == 0 or value < middle / _TOLERANCE or value > middle * _TOLERANCE:
                    found.append(f"{label} / {probe}: {name} answered {value} against a median of {middle:.0f}")
    return found


def main() -> None:
    """Report every disagreement; exit non-zero so a run can gate on the result."""
    workdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "bench-equivalence"
    workdir.mkdir(parents=True, exist_ok=True)
    found = disagreements(workdir)
    print("\n===== disagreements =====")
    for line in found or ["none"]:
        print(f"  {line}")
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()


__all__ = ["PROBES", "disagreements"]
