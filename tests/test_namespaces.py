"""The topic namespaces (:mod:`turbohtml.clean`, :mod:`turbohtml.extract`) re-export the expected surface."""

from __future__ import annotations

import pytest

import turbohtml
from turbohtml import clean, extract

_EXTRACT_RECORDS = ("Article", "Link", "MicrodataItem", "RdfaItem", "StructuredData")
_CLEAN_SANITIZE = (
    "DEFAULT_ATTRIBUTES",
    "DEFAULT_SCHEMES",
    "DEFAULT_TAGS",
    "OnDisallowed",
    "Policy",
    "Sanitizer",
    "sanitize",
)
_CLEAN_LINKIFY = ("Detector", "LinkSpan", "Linker", "Linkify", "linkify", "nofollow", "target_blank")


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
