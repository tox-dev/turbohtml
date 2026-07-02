"""
The ``bench-table`` directive: benchmark tables rendered from committed JSON feeds of raw measurements.

Every benchmark table pairs turbohtml against competitors. A feed carries only data -- a row label and one raw number
per party and metric (seconds for a time, bytes for a size), the shape ``tools/bench --table-json`` writes -- and this
directive derives everything shown: the readable unit for each figure, the ``(Nx)`` ratio against turbohtml, the
spanned party headers with metric subcolumns, and a tint on each cell from best-in-row green to worst-in-row red.

::

    .. bench-table::
        :file: bench/escaping.json

A feed is ``{"label", "parties", "metrics", "rows"}``; ``metrics`` is empty for a single time metric, and a row is the
label followed by one cell per party and metric -- a number, ``null`` for no entry, or a verbatim string such as
``parse error``. Column order is derived too: turbohtml first, then each competitor by its combined score with every
metric weighing equally, so the best speed/size combination sits leftmost whatever order the feed carries. The tint
scale is row-relative per metric -- a row's cells ran the same input, so they compare; a column mixes inputs, so it
does not -- with turbohtml competing at its defining 1.0x.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final

from docutils import nodes
from docutils.parsers.rst import Directive, directives

if TYPE_CHECKING:
    from sphinx.application import Sphinx

_LADDER: Final = ("faster", "par", "mild", "slow", "veryslow", "worst")

# The row's best ratio takes the green end and the rest log-interpolate toward red, but the scale never compresses
# below a minimum span, so a near-parity row reads green throughout instead of stretching a few percent across the
# whole ladder. A few percent of size is as telling as a multiple of time, so its minimum span is tighter.
_MIN_SPAN: Final = {"size": math.log(1.10), "time": math.log(8.0)}

# microseconds carry the micro sign the docs use throughout; the escape keeps the literal unambiguous
_TIME_UNITS: Final = (("s", 1.0), ("ms", 1e-3), ("\u00b5s", 1e-6), ("ns", 1e-9))

# every competitor header links to the project's home; a party absent here (the turbohtml columns) stays plain text
_HOMEPAGES: Final = {
    "airium": "https://pypi.org/project/airium/",
    "BeautifulSoup": "https://www.crummy.com/software/BeautifulSoup/",
    "bleach": "https://bleach.readthedocs.io/",
    "calmjs.parse": "https://github.com/calmjs/calmjs.parse",
    "chardet": "https://chardet.readthedocs.io/",
    "charset-normalizer": "https://charset-normalizer.readthedocs.io/",
    "css-html-js-minify": "https://pypi.org/project/css-html-js-minify/",
    "csscompressor": "https://github.com/sprymix/csscompressor",
    "cssmin": "https://github.com/zacharyvoase/cssmin",
    "dominate": "https://github.com/Knio/dominate",
    "extruct": "https://github.com/scrapinghub/extruct",
    "faust-cchardet": "https://github.com/faust-streaming/cChardet",
    "html-sanitizer": "https://github.com/matthiask/html-sanitizer",
    "html-text": "https://github.com/zytedata/html-text",
    "html.escape": "https://docs.python.org/3/library/html.html",
    "html.parser": "https://docs.python.org/3/library/html.parser.html",
    "html.unescape": "https://docs.python.org/3/library/html.html",
    "html2text": "https://github.com/Alir3z4/html2text",
    "html5-parser": "https://html5-parser.readthedocs.io/",
    "html5lib": "https://html5lib.readthedocs.io/",
    "htpy": "https://htpy.dev/",
    "inscriptis": "https://github.com/weblyzard/inscriptis",
    "jsmin": "https://github.com/tikitu/jsmin",
    "lightningcss": "https://pypi.org/project/lightningcss/",
    "linkify-it-py": "https://github.com/tsutsu3/linkify-it-py",
    "lxml": "https://lxml.de/",
    "lxml getpath": "https://lxml.de/",
    "lxml-html-clean": "https://lxml-html-clean.readthedocs.io/",
    "markdownify": "https://github.com/matthewwithanm/python-markdownify",
    "markupsafe": "https://markupsafe.palletsprojects.com/",
    "metadata_parser": "https://github.com/jvanasco/metadata_parser",
    "minify-html": "https://github.com/wilsonzlin/minify-html",
    "newspaper3k": "https://github.com/codelucas/newspaper",
    "nh3": "https://nh3.readthedocs.io/",
    "pandas": "https://pandas.pydata.org/",
    "pandas.read_html": "https://pandas.pydata.org/",
    "parsel": "https://parsel.readthedocs.io/",
    "pyquery": "https://pyquery.readthedocs.io/",
    "rcssmin": "https://opensource.perlig.de/rcssmin/",
    "rjsmin": "https://opensource.perlig.de/rjsmin/",
    "readability-lxml": "https://github.com/buriy/python-readability",
    "resiliparse": "https://resiliparse.chatnoir.eu/",
    "selectolax": "https://github.com/rushter/selectolax",
    "soupsieve": "https://facelessuser.github.io/soupsieve/",
    "terser": "https://terser.org/",
    "standard library": "https://docs.python.org/3/library/html.parser.html",
    "trafilatura": "https://trafilatura.readthedocs.io/",
    "w3lib": "https://w3lib.readthedocs.io/",
    "yattag": "https://www.yattag.org/",
}


def _format_time(seconds: float) -> str:
    for unit, factor in _TIME_UNITS:
        if seconds >= factor:
            return f"{seconds / factor:.3g} {unit}"
    return f"{seconds / 1e-9:.3g} ns"


def _format_size(size: float) -> str:
    return f"{size / 1000:.1f} kB" if size >= 1000 else f"{size:.0f} B"


def _format_ratio(ratio: float, metric: str) -> str:
    if metric == "size":
        # sizes sit near 1.0, so keep enough decimals to tell 0.999x from parity
        decimals = 2 if abs(ratio - 1) >= 0.005 else 3
        return f"({ratio:.{decimals}f}x)"
    # time ratios round up at the displayed precision, so a 0.04x never collapses to a flat 0.0x
    if ratio >= 100:
        return f"({math.ceil(ratio)}x)"
    return f"({math.ceil(ratio * 10) / 10:.1f}x)"


def _order_columns(parties: list[str], metrics: list[str], rows: list[list[Any]]) -> tuple[list[str], list[list[Any]]]:
    """
    Put turbohtml first, then each competitor by how close it comes overall, permuting every row to match.

    Each metric is averaged per party, scaled to [0, 1] across the parties on a log axis, and the metrics weigh
    equally, so with size and time present a size win counts as much as a time win instead of the time ratios --
    orders of magnitude larger -- deciding alone.
    """
    width = len(metrics)
    turbo_index = next(index for index, party in enumerate(parties) if "turbohtml" in party)

    def average_ratio(party_index: int, offset: int) -> float:
        ratios = [
            row[1 + party_index * width + offset] / row[1 + turbo_index * width + offset]
            for row in rows
            if isinstance(row[1 + party_index * width + offset], (int, float))
        ]
        return sum(ratios) / len(ratios) if ratios else math.inf

    scores = [0.0] * len(parties)
    for offset in range(width):
        averages = [average_ratio(party_index, offset) for party_index in range(len(parties))]
        finite = [math.log(value) for value in averages if math.isfinite(value)]
        low, high = min(finite), max(finite)
        span = high - low
        for party_index, value in enumerate(averages):
            scaled = (math.log(value) - low) / span if math.isfinite(value) and span else 0.0
            scores[party_index] += (scaled if math.isfinite(value) else 1.0) / width

    order = sorted(range(len(parties)), key=lambda index: ("turbohtml" not in parties[index], scores[index]))
    reordered = [
        [row[0], *[cell for party_index in order for cell in row[1 + party_index * width :][:width]]] for row in rows
    ]
    return [parties[party_index] for party_index in order], reordered


def _row_buckets(ratios: list[float], metric: str) -> list[str]:
    low = math.log(min(ratios))
    span = max(math.log(max(ratios)) - low, _MIN_SPAN.get(metric, _MIN_SPAN["time"]))
    steps = len(_LADDER) - 1
    return [_LADDER[round((math.log(ratio) - low) / span * steps)] for ratio in ratios]


class BenchTable(Directive):
    """Render one benchmark table from the JSON feed named by ``:file:``."""

    option_spec: ClassVar[dict[str, Any]] = {"file": directives.unchanged_required}

    def run(self) -> list[nodes.Node]:
        source = Path(self.state.document["source"]).parent / self.options["file"]
        self.state.document.settings.record_dependencies.add(str(source))
        feed = json.loads(source.read_text(encoding="utf-8"))
        metrics = feed["metrics"] or ["time"]
        parties, rows = _order_columns(feed["parties"], metrics, feed["rows"])
        ncols = 1 + len(parties) * len(metrics)
        table = nodes.table(classes=["bench-table"])
        group = nodes.tgroup(cols=ncols)
        table += group
        group += nodes.colspec(colwidth=2)
        for _ in range(ncols - 1):
            group += nodes.colspec(colwidth=1)
        group += self._head(feed["label"], parties, feed["metrics"])
        body = nodes.tbody()
        group += body
        for cells in rows:
            if len(cells) != ncols:
                msg = f"bench-table row has {len(cells)} cells, expected {ncols}: {cells!r}"
                raise self.error(msg)
            body += self._body_row(cells, parties, metrics)
        return [table]

    def _head(self, label: str, parties: list[str], metrics: list[str]) -> nodes.thead:
        head = nodes.thead()
        party_row = nodes.row()
        label_cell = self._entry(label)
        if metrics:
            label_cell["morerows"] = 1
        party_row += label_cell
        for party in parties:
            if (homepage := _HOMEPAGES.get(party)) is not None:
                cell = nodes.entry()
                paragraph = nodes.paragraph()
                link = nodes.reference(refuri=homepage)
                link += nodes.Text(party)
                paragraph += link
                cell += paragraph
            else:
                cell = self._entry(party)
            if metrics:
                cell["morecols"] = len(metrics) - 1
            party_row += cell
        head += party_row
        if metrics:
            metric_row = nodes.row()
            for _ in parties:
                for metric in metrics:
                    metric_row += self._entry(metric)
            head += metric_row
        return head

    def _body_row(self, cells: list[Any], parties: list[str], metrics: list[str]) -> nodes.row:
        row = nodes.row()
        row += self._entry(str(cells[0]))
        rendered: dict[int, tuple[str, str | None]] = {}
        for metric_index, metric in enumerate(metrics):
            turbo = next(
                cells[1 + party_index * len(metrics) + metric_index]
                for party_index, party in enumerate(parties)
                if "turbohtml" in party
            )
            positions: list[int] = []
            ratios: list[float] = []
            for party_index, party in enumerate(parties):
                index = 1 + party_index * len(metrics) + metric_index
                value = cells[index]
                if value is None:
                    rendered[index] = ("—", None)
                elif isinstance(value, str):
                    # a verbatim annotation such as ``parse error`` sits past any measured ratio
                    rendered[index] = (value, _LADDER[-1] if "error" in value else None)
                else:
                    figure = _format_size(value) if metric == "size" else _format_time(value)
                    ratio = value / turbo
                    if "turbohtml" not in party:
                        figure += f" {_format_ratio(ratio, metric)}"
                    positions.append(index)
                    ratios.append(ratio)
                    rendered[index] = (figure, None)
            for index, bucket in zip(positions, _row_buckets(ratios, metric), strict=True):
                rendered[index] = (rendered[index][0], bucket)
        for index in range(1, len(cells)):
            text, bucket = rendered[index]
            row += self._entry(text, bucket)
        return row

    def _entry(self, text: str, bucket: str | None = None) -> nodes.entry:
        entry = nodes.entry()
        paragraph = nodes.paragraph()
        # row labels and party names carry inline markup (``code``, :data:`x`), so parse rather than emit raw text
        children, messages = self.state.inline_text(text, self.lineno)
        paragraph += children
        entry += paragraph
        entry += messages
        if bucket is not None:
            entry["classes"].append(f"bench-{bucket}")
        return entry


def setup(app: Sphinx) -> dict[str, Any]:
    """Register the directive."""
    app.add_directive("bench-table", BenchTable)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
