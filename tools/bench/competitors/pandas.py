"""pandas: read_html, the one-call table reader scrapers reach for (returns DataFrames over lxml)."""

from __future__ import annotations

import io

import pandas as pd

REQUIREMENTS = ("pandas>=2.2", "lxml>=6.1.1")


def tables(case: tuple[str, str]) -> None:
    """Extract table grids with pandas.read_html: every table as rows, or the first table keyed by its header."""
    kind, text = case
    if kind == "rows":
        for frame in pd.read_html(io.StringIO(text)):
            frame.to_numpy().tolist()
    else:
        pd.read_html(io.StringIO(text), header=0)[0].to_dict("records")


OPERATIONS = {"tables": (tables, "pandas")}
