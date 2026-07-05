"""The topic namespaces (:mod:`turbohtml.clean`, :mod:`turbohtml.extract`) re-export the expected surface."""

from __future__ import annotations

import pytest

import turbohtml
from turbohtml import clean, extract, query

_EXTRACT_RECORDS = ("Article", "Link", "MicrodataItem", "RdfaItem", "StructuredData")
_QUERY_SURFACE = (
    "DEBUG",
    "Matcher",
    "Matching",
    "Query",
    "SelectorSyntaxError",
    "closest",
    "compile",
    "css",
    "escape_identifier",
    "filter",
    "iselect",
    "match",
    "select",
    "select_one",
)
_CLEAN_SANITIZE = (
    "DEFAULT_ATTRIBUTES",
    "DEFAULT_SCHEMES",
    "DEFAULT_TAGS",
    "OnDisallowed",
    "Policy",
    "Sanitizer",
    "sanitize",
)
_CLEAN_LINKIFY = (
    "LinkCandidate",
    "LinkDetector",
    "LinkSpan",
    "Linker",
    "Linkify",
    "linkify",
    "nofollow",
    "target_blank",
)


@pytest.mark.parametrize("name", [pytest.param(name, id=name) for name in _EXTRACT_RECORDS])
def test_extract_reexports_package_root_records(name: str) -> None:
    assert getattr(extract, name) is getattr(turbohtml, name)


@pytest.mark.parametrize("name", [pytest.param(name, id=name) for name in (*_CLEAN_SANITIZE, *_CLEAN_LINKIFY)])
def test_clean_exposes_sanitize_and_linkify(name: str) -> None:
    assert name in clean.__all__
    assert hasattr(clean, name)


def test_clean_namespace_is_functional() -> None:
    assert clean.sanitize("<script>x</script><b>ok</b>") == "&lt;script&gt;x&lt;/script&gt;<b>ok</b>"
    assert clean.linkify("see http://example.com").startswith("see <a ")


@pytest.mark.parametrize("name", [pytest.param(name, id=name) for name in _QUERY_SURFACE])
def test_query_folds_the_soupsieve_matching_surface(name: str) -> None:
    assert name in query.__all__
    assert hasattr(query, name)


def test_query_namespace_is_functional() -> None:
    assert [node.tag for node in query.Query("<a><b>x</b></a>")("b")] == ["b"]
    assert query.select("b", turbohtml.parse("<a><b>x</b></a>"))[0].tag == "b"
