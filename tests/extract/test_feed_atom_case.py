from __future__ import annotations

from typing import Final

import pytest

from turbohtml import parse
from turbohtml.extract import feed


def test_feed_atom_xml_root_case() -> None:
    assert feed('<FEED xmlns="http://www.w3.org/2005/Atom"><title>Wrong</title></FEED>') is None


def test_feed_atom_html_root_case() -> None:
    result = parse("<FEED><TITLE>Title</TITLE></FEED>").feed()
    assert result is not None
    assert result.title == "Title"


@pytest.mark.parametrize(
    ("name", "field"),
    [
        pytest.param("title", "title", id="title"),
        pytest.param("subtitle", "description", id="subtitle"),
        pytest.param("updated", "updated", id="updated"),
    ],
)
def test_feed_atom_xml_feed_field_case(name: str, field: str) -> None:
    xml: Final = (
        f'<feed xmlns="http://www.w3.org/2005/Atom"><{name.upper()}>Wrong</{name.upper()}><{name}>Right</{name}></feed>'
    )
    result = feed(xml)
    assert result is not None
    assert getattr(result, field) == "Right"


def test_feed_atom_xml_feed_link_case() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        '<LINK href="https://wrong.test"/><link href="https://right.test"/></feed>'
    )
    result = feed(xml)
    assert result is not None
    assert result.link == "https://right.test"


def test_feed_atom_xml_entry_case() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<ENTRY><title>Wrong</title></ENTRY><entry><title>Right</title></entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert tuple(entry.title for entry in result.entries) == ("Right",)


@pytest.mark.parametrize(
    ("name", "field"),
    [
        pytest.param("title", "title", id="title"),
        pytest.param("id", "id", id="id"),
        pytest.param("updated", "updated", id="updated"),
        pytest.param("published", "published", id="published"),
        pytest.param("summary", "summary", id="summary"),
        pytest.param("content", "content", id="content"),
    ],
)
def test_feed_atom_xml_entry_field_case(name: str, field: str) -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        f"<{name.upper()}>Wrong</{name.upper()}><{name}>Right</{name}>"
        "</entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert getattr(result.entries[0], field) == "Right"


def test_feed_atom_xml_entry_author_case() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        "<AUTHOR><name>Wrong</name></AUTHOR><author><NAME>Bad</NAME><name>Right</name></author>"
        "</entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].author == "Right"


def test_feed_atom_xml_entry_source_case() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        "<SOURCE><author><name>Wrong</name></author></SOURCE>"
        "<source><author><name>Right</name></author></source></entry></feed>"
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].author == "Right"


def test_feed_atom_xml_entry_link_case() -> None:
    xml: Final = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        '<LINK href="https://wrong.test"/><link href="https://right.test"/></entry></feed>'
    )
    result = feed(xml)
    assert result is not None
    assert result.entries[0].link == "https://right.test"
