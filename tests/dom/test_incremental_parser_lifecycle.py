"""Lifecycle and error surface of IncrementalParser: the spent-after-close state,
the context manager that discards an abandoned parse, and the argument checks."""

from __future__ import annotations

import pytest

from turbohtml import IncrementalParser


def test_feed_after_close_raises() -> None:
    parser = IncrementalParser()
    parser.feed("<p>x</p>")
    parser.close()
    with pytest.raises(ValueError, match="closed"):
        parser.feed("<p>more</p>")


def test_double_close_raises() -> None:
    parser = IncrementalParser()
    parser.feed("<p>x</p>")
    parser.close()
    with pytest.raises(ValueError, match="already closed"):
        parser.close()


def test_feed_rejects_other_types() -> None:
    parser = IncrementalParser()
    with pytest.raises(TypeError, match="str or a bytes-like object"):
        parser.feed(123)  # ty: ignore[invalid-argument-type]


def test_encoding_must_be_str() -> None:
    with pytest.raises(TypeError):
        IncrementalParser(encoding=b"utf-8")  # ty: ignore[invalid-argument-type]


def test_context_manager_closes_inside_block() -> None:
    with IncrementalParser() as parser:
        assert isinstance(parser, IncrementalParser)
        parser.feed("<p>hi</p>")
        document = parser.close()  # exit then sees a spent parser and does nothing
    paragraph = document.find("p")
    assert paragraph is not None
    assert paragraph.text == "hi"


def test_context_manager_discards_abandoned_parse() -> None:
    with IncrementalParser() as parser:
        parser.feed("<p>partial")
        # leaving the block without close() releases the in-progress C stream
    with pytest.raises(ValueError, match="closed"):
        parser.feed("<p>x</p>")


def test_parser_discarded_without_close() -> None:
    parser = IncrementalParser()
    parser.feed("<div><p>unfinished")
    del parser  # dealloc must free the in-progress stream without a close()


def test_default_encoding_is_utf8() -> None:
    parser = IncrementalParser()
    parser.feed("é".encode())  # decoded via the default utf-8 label
    assert parser.close().encoding == "UTF-8"
