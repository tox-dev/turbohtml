"""Sphinx configuration for the turbohtml documentation."""

from __future__ import annotations

import ast
from importlib.metadata import version as _version
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sphinx.application import Sphinx

project = "turbohtml"
author = "Bernát Gábor"
project_copyright = "2026, Bernát Gábor and contributors"
release = _version("turbohtml")
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
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

issues_github_path = "tox-dev/turbohtml"
towncrier_draft_autoversion_mode = "draft"
towncrier_draft_include_empty = True
towncrier_draft_working_directory = Path(__file__).parent.parent


def _stub_property_types() -> dict[tuple[str, str], str]:
    """
    Property return annotations from the _html.pyi stub, keyed by (class, name).

    sphinx-autodoc-typehints backfills function signatures from the stub, but C getset
    descriptors render as bare attributes; this lifts their types from the same stub
    so it stays the single source of truth.
    """
    import turbohtml  # noqa: PLC0415  # resolve the installed package the docs build against

    stub = Path(turbohtml.__file__).parent / "_html.pyi"
    types: dict[tuple[str, str], str] = {}
    for node in ast.parse(stub.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.ClassDef):
            continue
        for member in node.body:
            is_property = isinstance(member, ast.FunctionDef) and any(
                isinstance(decorator, ast.Name) and decorator.id == "property" for decorator in member.decorator_list
            )
            if is_property and member.returns is not None:
                types[node.name, member.name] = ast.unparse(member.returns)
    return types


_PROPERTY_TYPES = _stub_property_types()


def _inject_attribute_types(  # noqa: PLR0913, PLR0917  # the signature is fixed by the autodoc-process-docstring event
    app: Sphinx,  # noqa: ARG001
    what: str,
    name: str,
    obj: object,  # noqa: ARG001
    options: Any,  # noqa: ARG001, ANN401
    lines: list[str],
) -> None:
    if what != "attribute" or name.count(".") < 2:
        return
    _, cls, attribute = name.rsplit(".", 2)
    if (annotation := _PROPERTY_TYPES.get((cls, attribute))) is not None:
        lines.extend(["", f":type: {annotation}"])


def setup(app: Sphinx) -> None:
    """Register the stub-derived attribute typing hook."""
    app.connect("autodoc-process-docstring", _inject_attribute_types)
