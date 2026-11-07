"""
The ``bench-table`` directive: benchmark tables rendered from committed JSON feeds of raw measurements.

Every benchmark table pairs turbohtml against competitors. A feed carries only data -- a row label and one raw number
per party and metric (seconds for a time, bytes for a size), the shape ``tools/bench --table-json`` writes -- and this
directive derives everything shown: the readable unit for each figure, the ``(Nx)`` ratio against the leftmost party,
the spanned party headers with metric subcolumns, and a tint on each cell from best-in-row green to worst-in-row red.

::

    .. bench-table::
        :file: bench/escaping.json

A feed is ``{"label", "parties", "metrics", "rows"}``; ``metrics`` is empty for a single time metric, and a row is the
label followed by one cell per party and metric -- a number, or a string naming why the cell is empty (the message a
competitor threw on this input, or a note like ``no equivalent operation``). Each distinct reason in a table gets a
superscript, the empty cells show ``--`` with that superscript, and a legend centered under the table spells each one
out, so one marker never conflates two reasons. Column order is derived too: turbohtml first, then each competitor by
its combined score with every metric weighing equally, so the best speed/size combination sits leftmost whatever order
the feed carries. The tint scale is row-relative per metric -- a row's cells ran the same input, so they compare; a
column mixes inputs, so it does not -- with the leftmost party competing at its defining 1.0x. Every column but the
first carries its ratio, so a feed comparing turbohtml against itself (the interpreter table) reads like any other.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final

from docutils import nodes
from docutils.parsers.rst import Directive, directives

if TYPE_CHECKING:
    from sphinx.application import Sphinx

# an empty cell shows this glyph and a superscript keyed to the reason the legend spells out; a bare ``null`` in a feed
# predates recorded reasons, so it falls back to this generic note rather than rendering an unexplained blank
_GAP_MARK: Final[str] = "—"
_MISSING_REASON: Final[str] = "no measurement recorded"

# The row's best ratio takes the green end and the rest log-interpolate toward red, but the scale never compresses
# below a minimum span, so a near-parity row reads green throughout instead of stretching a few percent across the
# whole ladder. A few percent of size is as telling as a multiple of time, so its minimum span is tighter; peak memory
# spans multiples like time, so it shares time's wider span.
_TIME_SPAN: Final[float] = math.log(8.0)
_MIN_SPAN: Final[dict[str, float]] = {"size": math.log(1.10), "time": _TIME_SPAN, "memory": _TIME_SPAN}

# microseconds carry the micro sign the docs use throughout; the escape keeps the literal unambiguous
_TIME_UNITS: Final = (("s", 1.0), ("ms", 1e-3), ("\u00b5s", 1e-6), ("ns", 1e-9))

# below this the uncertainty rounds to nothing at the shown precision, so printing it only adds noise to the table
_RATIO_SPREAD_FLOOR: Final[float] = 0.005

# every competitor header links to the project's home; a party absent here (the turbohtml columns) stays plain text
_HOMEPAGES: Final[dict[str, str]] = {
    "airium": "https://pypi.org/project/airium/",
    "BeautifulSoup": "https://www.crummy.com/software/BeautifulSoup/",
    "bleach": "https://bleach.readthedocs.io/",
    "boilerpy3": "https://github.com/jmriebold/BoilerPy3",
    "calmjs.parse": "https://github.com/calmjs/calmjs.parse",
    "chardet": "https://chardet.readthedocs.io/",
    "charset-normalizer": "https://charset-normalizer.readthedocs.io/",
    "courlan": "https://github.com/adbar/courlan",
    "css-html-js-minify": "https://pypi.org/project/css-html-js-minify/",
    "csscompressor": "https://github.com/sprymix/csscompressor",
    "cssmin": "https://github.com/zacharyvoase/cssmin",
    "cssselect": "https://github.com/scrapy/cssselect",
    "dominate": "https://github.com/Knio/dominate",
    "DOMPurify": "https://github.com/cure53/DOMPurify",
    "extruct": "https://github.com/scrapinghub/extruct",
    "fast-html": "https://github.com/pcarbonn/fast_html",
    "faust-cchardet": "https://github.com/faust-streaming/cChardet",
    "goose3": "https://goose3.readthedocs.io/",
    "html-sanitizer": "https://github.com/matthiask/html-sanitizer",
    "html-text": "https://github.com/zytedata/html-text",
    "html.escape": "https://docs.python.org/3/library/html.html",
    "html.parser": "https://docs.python.org/3/library/html.parser.html",
    "html.unescape": "https://docs.python.org/3/library/html.html",
    "html2text": "https://github.com/Alir3z4/html2text",
    "html5-parser": "https://html5-parser.readthedocs.io/",
    "html5lib": "https://html5lib.readthedocs.io/",
    "htmldate": "https://htmldate.readthedocs.io/",
    "htbuilder": "https://github.com/tvst/htbuilder",
    "htpy": "https://htpy.dev/",
    "hyperpython": "https://github.com/fabiommendes/hyperpython",
    "inscriptis": "https://github.com/weblyzard/inscriptis",
    "jsmin": "https://github.com/tikitu/jsmin",
    "justext": "https://github.com/miso-belica/jusText",
    "lightningcss": "https://pypi.org/project/lightningcss/",
    "linkify-it-py": "https://github.com/tsutsu3/linkify-it-py",
    "lxml": "https://lxml.de/",
    "lxml getpath": "https://lxml.de/",
    "lxml-html-clean": "https://lxml-html-clean.readthedocs.io/",
    "markdownify": "https://github.com/matthewwithanm/python-markdownify",
    "markupsafe": "https://markupsafe.palletsprojects.com/",
    "markyp": "https://github.com/volfpeter/markyp-html",
    "metadata_parser": "https://github.com/jvanasco/metadata_parser",
    "minify-html": "https://github.com/wilsonzlin/minify-html",
    "news-please": "https://github.com/fhamborg/news-please",
    "newspaper3k": "https://github.com/codelucas/newspaper",
    "nh3": "https://nh3.readthedocs.io/",
    "pandas": "https://pandas.pydata.org/",
    "pandas.read_html": "https://pandas.pydata.org/",
    "parse5": "https://github.com/inikulin/parse5",
    "parsel": "https://parsel.readthedocs.io/",
    "pyquery": "https://pyquery.readthedocs.io/",
    "rcssmin": "https://opensource.perlig.de/rcssmin/",
    "rjsmin": "https://opensource.perlig.de/rjsmin/",
    "sanitize-html": "https://github.com/apostrophecms/sanitize-html",
    "readability-lxml": "https://github.com/buriy/python-readability",
    "readabilipy": "https://readabilipy.readthedocs.io/",
    "resiliparse": "https://resiliparse.chatnoir.eu/",
    "selectolax": "https://github.com/rushter/selectolax",
    "simple-html": "https://github.com/keithasaurus/simple_html",
    "soupsieve": "https://facelessuser.github.io/soupsieve/",
    "terser": "https://terser.org/",
    "esbuild": "https://esbuild.github.io/",
    "tdewolff": "https://github.com/tdewolff/minify",
    "html-minifier-terser": "https://github.com/terser/html-minifier-terser",
    "standard library": "https://docs.python.org/3/library/html.parser.html",
    "trafilatura": "https://trafilatura.readthedocs.io/",
    "w3lib": "https://w3lib.readthedocs.io/",
    "yattag": "https://www.yattag.org/",
}


def setup(app: Sphinx) -> dict[str, Any]:
    app.add_directive("bench-table", BenchTable)
    return {"parallel_read_safe": True, "parallel_write_safe": True}


class BenchTable(Directive):
    # Render one benchmark table from the JSON feed named by ``:file:``.
    option_spec: ClassVar[dict[str, Any]] = {"file": directives.unchanged_required}

    def run(self) -> list[nodes.Node]:
        source = Path(self.state.document["source"]).parent / self.options["file"]
        self.state.document.settings.record_dependencies.add(str(source))
        feed = json.loads(source.read_text(encoding="utf-8"))
        metrics = feed["metrics"] or ["time"]
        parties, rows, spread = _order_columns(feed["parties"], metrics, feed["rows"], feed.get("spread") or [])
        ncols = 1 + len(parties) * len(metrics)
        table, group = _table_frame(ncols)
        layout = _Layout(parties=parties, metrics=metrics, reasons=_reason_map(rows))
        # notes number on from the empty-cell reasons so one legend carries both without colliding ordinals
        notes = {
            party: len(layout.reasons) + offset for offset, party in enumerate(_ordered_notes(feed, parties), start=1)
        }
        row_notes, row_marks = _row_caveats(feed, len(layout.reasons) + len(notes))
        group += self._head(feed["label"], parties, feed["metrics"], notes)
        group += self._body(rows, spread, layout, row_marks, ncols)
        legend = _merge_legend(layout.reasons, notes, row_notes, feed)
        return [table] if not legend else [table, _legend(legend)]

    def _head(self, label: str, parties: list[str], metrics: list[str], notes: dict[str, int]) -> nodes.thead:
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
            # a party whose column answers a different question is marked at the header, not per cell: the caveat
            # applies to everything below it
            if (ordinal := notes.get(party)) is not None:
                superscript = nodes.superscript()
                superscript += nodes.Text(str(ordinal))
                cell.children[0] += superscript
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

    def _body(
        self,
        rows: list[list[Any]],
        spread: list[Any],
        layout: _Layout,
        row_marks: dict[int, int],
        ncols: int,
    ) -> nodes.tbody:
        body = nodes.tbody()
        for index, cells in enumerate(rows):
            if len(cells) != ncols:
                msg = f"bench-table row has {len(cells)} cells, expected {ncols}: {cells!r}"
                raise self.error(msg)
            body += self._body_row(cells, layout, spread[index] if index < len(spread) else None, row_marks.get(index))
        return body

    def _body_row(
        self, cells: list[Any], layout: _Layout, spread: list[Any] | None = None, caveat: int | None = None
    ) -> nodes.row:
        parties, metrics, reasons = layout.parties, layout.metrics, layout.reasons
        row = nodes.row()
        label = self._entry(str(cells[0]))
        # a caveat that belongs to one operation marks that row, not the library's whole column
        if caveat is not None:
            superscript = nodes.superscript()
            superscript += nodes.Text(str(caveat))
            label.children[0] += superscript
        row += label
        rendered: dict[int, tuple[str, str | None]] = {}
        marks: dict[int, int] = {}
        for metric_index, metric in enumerate(metrics):
            # _order_columns has already put the baseline first, so column 0 is what every ratio is read against; a
            # feed whose columns are all turbohtml (the interpreter comparison) still gets a ratio on every other one
            turbo = cells[1 + metric_index]
            positions: list[int] = []
            ratios: list[float] = []
            for party_index in range(len(parties)):
                index = 1 + party_index * len(metrics) + metric_index
                value = cells[index]
                if isinstance(value, (int, float)):
                    if metric == "size":
                        figure = _format_size(value)
                    elif metric == "memory":
                        figure = _format_memory(value)
                    else:
                        figure = _format_time(value)
                    ratio = value / turbo
                    if party_index:
                        figure += f" {_format_ratio(ratio, metric, _ratio_spread(spread, index, 1 + metric_index))}"
                    positions.append(index)
                    ratios.append(ratio)
                    rendered[index] = (figure, None)
                else:  # an empty cell points at the reason spelled out in the legend
                    marks[index] = reasons[value if isinstance(value, str) else _MISSING_REASON]
            for index, bucket in zip(positions, _row_buckets(ratios, metric), strict=True):
                rendered[index] = (rendered[index][0], bucket)
        for index in range(1, len(cells)):
            row += _mark_entry(marks[index]) if index in marks else self._entry(*rendered[index])
        return row


def _order_columns(
    parties: list[str], metrics: list[str], rows: list[list[Any]], spread: list[list[Any]]
) -> tuple[list[str], list[list[Any]], list[list[Any]]]:
    # Put turbohtml first, then each competitor by how close it comes overall, permuting every row to match.
    #
    # Each metric is averaged per party, scaled to [0, 1] across the parties on a log axis, and the metrics weigh
    # equally, so with size and time present a size win counts as much as a time win instead of the time ratios --
    # orders of magnitude larger -- deciding alone.
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

    def permute(source: list[list[Any]]) -> list[list[Any]]:
        return [
            [row[0], *[cell for party_index in order for cell in row[1 + party_index * width :][:width]]]
            for row in source
        ]

    # the spread shares the row layout, so it has to travel through the same permutation or a cell would inherit
    # another party's uncertainty
    return [parties[party_index] for party_index in order], permute(rows), permute(spread) if spread else []


def _table_frame(ncols: int) -> tuple[nodes.table, nodes.tgroup]:
    # Build the table and its column group: a wide first column for the row label, then one per metric column.
    table = nodes.table(classes=["bench-table"])
    group = nodes.tgroup(cols=ncols)
    table += group
    group += nodes.colspec(colwidth=2)
    for _ in range(ncols - 1):
        group += nodes.colspec(colwidth=1)
    return table, group


@dataclass(frozen=True, slots=True, kw_only=True)
class _Layout:
    # What every row of one table shares: its columns, its metrics, and the numbered reasons its cells key into.
    parties: list[str]
    metrics: list[str]
    reasons: dict[str, int]


def _reason_map(rows: list[list[Any]]) -> dict[str, int]:
    # Assign each distinct empty-cell reason a number in first-appearance order, scanning rows then cells.
    reasons: dict[str, int] = {}
    for row in rows:
        for cell in row[1:]:
            if isinstance(cell, (int, float)):
                continue
            if (reason := cell if isinstance(cell, str) else _MISSING_REASON) not in reasons:
                reasons[reason] = len(reasons) + 1
    return reasons


def _ordered_notes(feed: dict[str, Any], parties: list[str]) -> list[str]:
    # Return the noted parties in the order their columns appear, so ordinals read left to right.
    notes = feed.get("notes") or {}
    return [party for party in parties if party in notes]


def _row_caveats(feed: dict[str, Any], taken: int) -> tuple[dict[str, int], dict[int, int]]:
    # Assign each distinct per-row caveat an ordinal after those already taken, and map each row to its ordinal.
    notes: dict[str, int] = {}
    marks: dict[int, int] = {}
    for index, note in sorted((int(key), value) for key, value in (feed.get("row_notes") or {}).items()):
        notes.setdefault(note, taken + len(notes) + 1)
        marks[index] = notes[note]
    return notes, marks


def _merge_legend(
    reasons: dict[str, int], notes: dict[str, int], row_notes: dict[str, int], feed: dict[str, Any]
) -> dict[str, int]:
    # Gather the empty-cell reasons, the column notes and the row caveats into one numbered legend.
    legend = dict(reasons)
    for party, ordinal in notes.items():
        legend[f"{party} {feed['notes'][party]}"] = ordinal
    legend.update(row_notes)
    return legend


def _legend(reasons: dict[str, int]) -> nodes.container:
    # Spell each numbered reason out under the table; reason text is literal so a message cannot inject markup.
    legend = nodes.container(classes=["bench-legend"])
    for reason, ordinal in reasons.items():
        line = nodes.paragraph()
        superscript = nodes.superscript()
        superscript += nodes.Text(str(ordinal))
        line += superscript
        line += nodes.Text(f" {reason}")
        legend += line
    return legend


def _format_size(size: float) -> str:
    for unit, factor in (("MB", 1e6), ("kB", 1e3)):
        if size >= factor:
            return _fixed(size / factor, unit)
    return f"{size:.0f} B"


def _fixed(value: float, unit: str) -> str:
    # Render a scaled figure in plain decimal: three significant digits, grouped once it runs past a thousand.
    if value >= 1000:
        return f"{value:,.0f} {unit}"
    return f"{value:.3g} {unit}"


def _format_memory(size: float) -> str:
    for unit, factor in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if size >= factor:
            return _fixed(size / factor, unit)
    return f"{size:.0f} B"


def _format_time(seconds: float) -> str:
    # Scale a duration to the largest unit it fills; never exponent notation, which reads as a defect in a table.
    for unit, factor in _TIME_UNITS:
        if seconds >= factor:
            return _fixed(seconds / factor, unit)
    return _fixed(seconds / 1e-9, "ns")


def _format_ratio(ratio: float, metric: str, spread: float | None) -> str:
    if metric == "size":
        # sizes sit near 1.0, so keep enough decimals to tell 0.999x from parity
        return f"({ratio:.{(2 if abs(ratio - 1) >= 0.005 else 3)}f}x)"
    # time ratios round up at the displayed precision, so a 0.04x never collapses to a flat 0.0x
    shown = f"{math.ceil(ratio)}x" if ratio >= 100 else f"{math.ceil(ratio * 10) / 10:.1f}x"
    if spread is None or spread < _RATIO_SPREAD_FLOOR:
        return f"({shown})"
    return f"({shown} ±{spread * 100:.0f}%)"


def _ratio_spread(spread: list[Any] | None, index: int, turbo_index: int) -> float | None:
    # Return the relative uncertainty of one ratio, or None when either side carries no spread.
    #
    # The two timings are measured independently, so their relative uncertainties add in quadrature. A ratio quoted
    # without it reads firmer than the measurements under it actually are.
    if not spread:
        return None
    numerator, denominator = spread[index], spread[turbo_index]
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
        return None
    return math.hypot(numerator, denominator)


def _row_buckets(ratios: list[float], metric: str) -> list[str]:
    low = math.log(min(ratios))
    span = max(math.log(max(ratios)) - low, _MIN_SPAN.get(metric, _MIN_SPAN["time"]))
    ladder = ("faster", "par", "mild", "slow", "veryslow", "worst")
    steps = len(ladder) - 1
    return [ladder[round((math.log(ratio) - low) / span * steps)] for ratio in ratios]


def _mark_entry(ordinal: int) -> nodes.entry:
    # Render an empty cell as the gap glyph plus the superscript keying it to the legend.
    entry = nodes.entry(classes=["bench-na"])
    paragraph = nodes.paragraph()
    paragraph += nodes.Text(_GAP_MARK)
    superscript = nodes.superscript()
    superscript += nodes.Text(str(ordinal))
    paragraph += superscript
    entry += paragraph
    return entry
