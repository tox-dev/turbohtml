"""Sphinx configuration for the turbohtml documentation."""

from __future__ import annotations

import ast
import copy
import re
import sys
from importlib.metadata import version as _version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docutils import nodes
from docutils.parsers.rst import Directive

if TYPE_CHECKING:
    from sphinx.application import Sphinx

sys.path.insert(0, str(Path(__file__).parent / "_ext"))

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
    "sphinxcontrib.mermaid",  # the .. mermaid:: directive used by the explanation diagrams
    "sphinxcontrib.towncrier.ext",  # render unreleased news fragments as a draft section
    "bench_table",  # the .. bench-table:: directive rendering the benchmark tables from a data feed (docs/_ext)
]

html_theme = "furo"
html_title = "turbohtml"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_logo = "_static/turbohtml.svg"
html_favicon = "_static/turbohtml.svg"

# News fragments are assembled by towncrier, not rendered as standalone pages.
exclude_patterns = ["changelog/*"]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "markupsafe": ("https://markupsafe.palletsprojects.com/en/stable/", None),
    "parsel": ("https://parsel.readthedocs.io/en/latest/", None),
    "w3lib": ("https://w3lib.readthedocs.io/en/latest/", None),
    "html5lib": ("https://html5lib.readthedocs.io/en/latest/", None),
}
autodoc_member_order = "bysource"
nitpicky = True
always_document_param_types = True
# Annotations sourced from the type stubs are fully qualified (collections.abc.Mapping, re.Pattern, ...) so the
# Python domain can resolve their cross-references; display them under their short names to match the rest of the docs.
python_use_unqualified_type_names = True
# _Filter is the private recursive alias for find()/find_all() filters; the stub-sourced signatures render it by name,
# and it is intentionally not a documented target. JSONValue is a recursive type alias, so its expansion references
# itself and cannot resolve to a single doc target; StructuredData and MicrodataItem are real autodoc'd classes now, so
# their cross-references resolve and need no ignore.
nitpick_ignore = [
    ("py:type", "_Filter"),
    ("py:class", "_Filter"),
    ("py:type", "JSONValue"),
    ("py:class", "JSONValue"),
    # Signal is the Literal alias PublicationDate.signal is typed as; autodoc renders it by name, and the enumerated
    # values (not a class) are spelled out in the field's docstring, so it is intentionally not a cross-ref target.
    ("py:class", "Signal"),
    ("py:type", "Signal"),
]

issues_github_path = "tox-dev/turbohtml"
towncrier_draft_autoversion_mode = "draft"
towncrier_draft_include_empty = True
towncrier_draft_working_directory = Path(__file__).parent.parent


# The compiled ``turbohtml._html`` extension exposes only ``__text_signature__`` (parameter names and defaults, no
# types) and bare ``getset`` descriptors, so ``sphinx-autodoc-typehints`` -- which reads ``typing.get_type_hints`` --
# finds nothing to render. The full annotations already live in the ``*.pyi`` stubs, which are the single source of
# types: parse them and feed the typed signatures back into autodoc, keeping the hand-written docstring prose intact.
class _QualifyNames(ast.NodeTransformer):
    """Rewrite stub names imported from non-turbohtml modules to their fully qualified dotted form."""

    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases

    def visit_Name(self, node: ast.Name) -> ast.expr:
        if (qualified := self.aliases.get(node.id)) is not None:
            return ast.copy_location(ast.parse(qualified, mode="eval").body, node)
        return node


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and not node.module.startswith("turbohtml")
        ):
            for name in node.names:
                aliases[name.asname or name.name] = f"{node.module}.{name.name}"
    return aliases


def _render_signature(
    func: ast.FunctionDef, aliases: dict[str, str], *, drop_self: bool, keep_return: bool
) -> tuple[str, str | None]:
    arguments = copy.deepcopy(func.args)
    returns = copy.deepcopy(func.returns) if keep_return else None
    _QualifyNames(aliases).visit(arguments)
    if returns is not None:
        returns = _QualifyNames(aliases).visit(returns)
    if drop_self:
        if arguments.posonlyargs and arguments.posonlyargs[0].arg in {"self", "cls"}:
            arguments.posonlyargs.pop(0)
        elif arguments.args and arguments.args[0].arg in {"self", "cls"}:
            arguments.args.pop(0)
    dummy = ast.FunctionDef(
        name="_", args=arguments, body=[ast.Expr(ast.Constant(...))], decorator_list=[], returns=returns, type_params=[]
    )
    ast.fix_missing_locations(dummy)
    header = ast.unparse(dummy).splitlines()[0]
    matched = re.match(r"^def _(\(.*\))(?: -> (.*))?:$", header)
    assert matched is not None  # noqa: S101 -- a one-line ``def`` header always matches
    return matched.group(1), matched.group(2)


def _collect_stub_types() -> tuple[dict[str, tuple[str, str | None]], dict[str, str]]:
    signatures: dict[str, tuple[str, str | None]] = {}
    property_types: dict[str, str] = {}
    for stub in sorted((Path(__file__).parent.parent / "src" / "turbohtml" / "_stubs").glob("*.pyi")):
        tree = ast.parse(stub.read_text(encoding="utf-8"))
        aliases = _import_aliases(tree)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                signatures[f"turbohtml.{node.name}"] = _render_signature(
                    node, aliases, drop_self=False, keep_return=True
                )
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if not isinstance(item, ast.FunctionDef):
                        continue
                    decorators = item.decorator_list
                    if any(
                        isinstance(deco, ast.Attribute) and deco.attr in {"setter", "deleter"} for deco in decorators
                    ):
                        continue
                    key = f"turbohtml.{node.name}.{item.name}"
                    if any(isinstance(deco, ast.Name) and deco.id == "property" for deco in decorators):
                        property_types[key] = (
                            _render_signature(item, aliases, drop_self=True, keep_return=True)[1] or ""
                        )
                    elif item.name == "__init__":
                        arguments, _ = _render_signature(item, aliases, drop_self=True, keep_return=False)
                        signatures[f"turbohtml.{node.name}"] = (arguments, None)
                    else:
                        signatures[key] = _render_signature(item, aliases, drop_self=True, keep_return=True)
    return signatures, property_types


_STUB_SIGNATURES, _STUB_PROPERTY_TYPES = _collect_stub_types()

# turbohtml.migration.markupsafe re-exports the compiled ``_markup_*`` helpers under their markupsafe names; autodoc
# sees the alias full name, so point each one at the stub entry that carries its typed signature.
_STUB_ALIASES = {
    "turbohtml.migration.markupsafe.escape": "turbohtml._markup_escape",
    "turbohtml.migration.markupsafe.escape_silent": "turbohtml._markup_escape_silent",
    "turbohtml.migration.markupsafe.soft_str": "turbohtml._markup_soft_str",
}


def _patch_autodoc_engine() -> None:
    # Sphinx 9's functional autodoc engine builds the signature and the property/attribute ``:type:`` purely from
    # runtime introspection, which yields nothing for the compiled extension. There is no public hook that can supply
    # types for a C object, so feed the stub-sourced values into the two private engine functions that produce them.
    import dataclasses

    from sphinx.ext.autodoc._dynamic import _loader, _signatures

    extract_signature = _signatures._extract_signature_from_object

    def _extract_signature(*, props: Any, **kwargs: Any) -> list[tuple[str, str]]:
        full_name = _STUB_ALIASES.get(props.full_name, props.full_name)
        if (entry := _STUB_SIGNATURES.get(full_name)) is not None:
            arguments, return_annotation = entry
            return [(arguments, return_annotation or "")]
        return extract_signature(props=props, **kwargs)

    _signatures._extract_signature_from_object = _extract_signature

    make_props = _loader._make_props_from_imported_object

    def _make_props(*args: Any, **kwargs: Any) -> Any:
        props = make_props(*args, **kwargs)
        if props is None or (annotation := _STUB_PROPERTY_TYPES.get(props.full_name)) is None:
            return props
        if props.obj_type == "property":
            return dataclasses.replace(props, _obj_property_type_annotation=annotation)
        if props.obj_type in {"attribute", "data"}:
            return dataclasses.replace(props, _obj_type_annotation=annotation)
        return props

    _loader._make_props_from_imported_object = _make_props


def _stub_signature_for_alias(  # noqa: PLR0913, PLR0917 -- the signature is fixed by the autodoc-process-signature event
    app: Sphinx,  # noqa: ARG001
    what: str,  # noqa: ARG001
    name: str,
    obj: object,  # noqa: ARG001
    options: Any,  # noqa: ARG001
    signature: str | None,  # noqa: ARG001
    return_annotation: str | None,  # noqa: ARG001
) -> tuple[str, str] | None:
    """
    Supply the typed signature for the markupsafe aliases, whose compiled docstring carries an untyped one.

    Their docstring's leading ``name(sig)`` line gives autodoc a signature before it reaches the stub hook, so the
    only place left to inject the stub-sourced types is the ``autodoc-process-signature`` event.
    """
    if (entry := _STUB_SIGNATURES.get(_STUB_ALIASES.get(name, ""))) is None:
        return None
    arguments, return_type = entry
    return arguments, return_type or ""


class _PackageMeta(Directive):
    """
    Render one badge row of package metadata: ``.. package-meta:: <pypi-name> [github-slug]``.

    Every migration guide opens with the same at-a-glance facts about the library it replaces (latest release,
    supported Pythons, license, downloads, and -- when the project lives on GitHub -- stars and recency), so a single
    directive keeps the set and its order identical across the guides instead of hand-maintaining badge stacks.
    """

    required_arguments = 1
    optional_arguments = 1

    def run(self) -> list[nodes.Node]:
        pypi = self.arguments[0]
        badges = [
            (
                "latest release",
                f"https://img.shields.io/pypi/v/{pypi}?label=release",
                f"https://pypi.org/project/{pypi}/",
            ),
            (
                "supported Pythons",
                f"https://img.shields.io/pypi/pyversions/{pypi}",
                f"https://pypi.org/project/{pypi}/",
            ),
            ("license", f"https://img.shields.io/pypi/l/{pypi}", f"https://pypi.org/project/{pypi}/"),
            ("monthly downloads", f"https://static.pepy.tech/badge/{pypi}/month", f"https://pepy.tech/project/{pypi}"),
            ("total downloads", f"https://static.pepy.tech/badge/{pypi}", f"https://pepy.tech/project/{pypi}"),
        ]
        if len(self.arguments) > 1:
            slug = self.arguments[1]
            badges += [
                ("GitHub stars", f"https://img.shields.io/github/stars/{slug}", f"https://github.com/{slug}"),
                ("last commit", f"https://img.shields.io/github/last-commit/{slug}", f"https://github.com/{slug}"),
            ]
        row = nodes.paragraph(classes=["package-meta"])
        for alt, image, target in badges:
            link = nodes.reference(refuri=target)
            link += nodes.image(uri=image, alt=f"{pypi} {alt}")
            row += link
        return [row]


def setup(app: Sphinx) -> dict[str, Any]:
    """Wire the stub-sourced type annotations into autodoc and register the package-meta badge row."""
    _patch_autodoc_engine()
    app.connect("autodoc-process-signature", _stub_signature_for_alias)
    app.add_directive("package-meta", _PackageMeta)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
