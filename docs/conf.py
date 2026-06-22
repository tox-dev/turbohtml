"""Sphinx configuration for the turbohtml documentation."""

from __future__ import annotations

from importlib.metadata import version as _version
from pathlib import Path

project = "turbohtml"
author = "Bernát Gábor"
project_copyright = "2026, Bernát Gábor and contributors"
release = _version("turbohtml")
version = ".".join(release.split(".")[:2])

# The how-to and migration guides are split across one page per topic/library, and each page is its own doctest
# group, so the shared ``import turbohtml`` / ``from turbohtml import parse`` the recipes lean on is set up here.
doctest_global_setup = "import turbohtml\nfrom turbohtml import parse"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.doctest",  # run the testcode/testoutput examples so the docs cannot drift from the code
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
    "sphinx_issues",  # the :issue: role used by the changelog
    "sphinxcontrib.towncrier.ext",  # render unreleased news fragments as a draft section
]

html_theme = "furo"
html_title = "turbohtml"

# News fragments are assembled by towncrier, not rendered as standalone pages.
exclude_patterns = ["changelog/*"]

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}
autodoc_member_order = "bysource"
nitpicky = True
always_document_param_types = True
# _Filter is the private recursive alias for find()/find_all() filters; the
# typehints extension expands it inline, so the bare name is never a doc target.
nitpick_ignore = [("py:type", "_Filter")]

issues_github_path = "tox-dev/turbohtml"
towncrier_draft_autoversion_mode = "draft"
towncrier_draft_include_empty = True
towncrier_draft_working_directory = Path(__file__).parent.parent
