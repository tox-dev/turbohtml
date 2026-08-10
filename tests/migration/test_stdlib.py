"""The html.parser-compatible callback adapter over the streaming tokenizer."""

from __future__ import annotations

import pytest

from turbohtml.migration.stdlib import HTMLParser


class _Recorder(HTMLParser):
    """Records every callback as a tuple, for asserting the dispatched event stream."""

    def __init__(self, *, convert_charrefs: bool = True) -> None:
        super().__init__(convert_charrefs=convert_charrefs)
        self.events: list[tuple[object, ...]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.events.append(("start", tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.events.append(("startend", tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        self.events.append(("end", tag))

    def handle_data(self, data: str) -> None:
        self.events.append(("data", data))

    def handle_comment(self, data: str) -> None:
        self.events.append(("comment", data))

    def handle_decl(self, decl: str) -> None:
        self.events.append(("decl", decl))

    def handle_pi(self, data: str) -> None:
        self.events.append(("pi", data))


def _events(html: str) -> list[tuple[object, ...]]:
    parser = _Recorder()
    parser.feed(html)
    parser.close()
    return parser.events


@pytest.mark.parametrize(
    ("html", "events"),
    [
        pytest.param("<p>", [("start", "p", [])], id="start-tag"),
        pytest.param('<p class="x" id=y>', [("start", "p", [("class", "x"), ("id", "y")])], id="start-tag-attrs"),
        pytest.param("</p>", [("end", "p")], id="end-tag"),
        pytest.param("<br/>", [("startend", "br", [])], id="self-closing"),
        pytest.param("text", [("data", "text")], id="text"),
        pytest.param("<!--c-->", [("comment", "c")], id="comment"),
        pytest.param("<!doctype html>", [("decl", "DOCTYPE html")], id="doctype"),
        # references are resolved into the text, so handle_data sees decoded characters
        pytest.param("a &amp; b &#9731;", [("data", "a & b ☃")], id="charrefs-decoded"),
        # a valueless attribute's value is the empty string, not None
        pytest.param("<input disabled>", [("start", "input", [("disabled", "")])], id="valueless-attr"),
        pytest.param("<?proc?>", [("pi", "proc")], id="processing-instruction"),
        pytest.param("<?proc data?>", [("pi", "proc data")], id="processing-instruction-data"),
        pytest.param("<![CDATA[x]]>", [("comment", "[CDATA[x]]")], id="cdata-is-comment"),
    ],
)
def test_dispatch(html: str, events: list[tuple[object, ...]]) -> None:
    assert _events(html) == events


@pytest.mark.parametrize(
    ("html", "decl"),
    [
        pytest.param("<!doctype html>", "DOCTYPE html", id="name-only"),
        pytest.param("<!doctype>", "DOCTYPE", id="no-name"),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD//EN" "http://x">',
            'DOCTYPE html PUBLIC "-//W3C//DTD//EN" "http://x"',
            id="public-and-system",
        ),
        pytest.param('<!DOCTYPE html PUBLIC "pub">', 'DOCTYPE html PUBLIC "pub"', id="public-only"),
        pytest.param(
            '<!DOCTYPE html SYSTEM "about:legacy-compat">',
            'DOCTYPE html SYSTEM "about:legacy-compat"',
            id="system-only",
        ),
    ],
)
def test_doctype_decl(html: str, decl: str) -> None:
    assert _events(html) == [("decl", decl)]


def test_full_document_event_stream() -> None:
    html = "<!doctype html><p class=intro>Tom &amp; Jerry<br/><!--c--></p>"
    assert _events(html) == [
        ("decl", "DOCTYPE html"),
        ("start", "p", [("class", "intro")]),
        ("data", "Tom & Jerry"),
        ("startend", "br", []),
        ("comment", "c"),
        ("end", "p"),
    ]


def test_incremental_feed_matches_one_shot() -> None:
    chunks = ["<ul><li>on", "e<li>tw", "o</ul>"]
    incremental = _Recorder()
    for chunk in chunks:
        incremental.feed(chunk)
    incremental.close()
    assert incremental.events == _events("".join(chunks))


def test_default_startendtag_fires_start_then_end() -> None:
    # the base handle_startendtag dispatches start then end, like the standard library
    class StartEnd(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.events: list[tuple[object, ...]] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            self.events.append(("start", tag, attrs))

        def handle_endtag(self, tag: str) -> None:
            self.events.append(("end", tag))

    parser = StartEnd()
    parser.feed("<br/>")
    parser.close()
    assert parser.events == [("start", "br", []), ("end", "br")]


def test_default_handlers_are_no_ops() -> None:
    # the base class ignores every token without raising
    parser = HTMLParser()
    parser.feed("<!doctype html><p>x &amp; y<br/><!--c--></p><?pi?>")
    parser.close()
    # the never-called compatibility hooks exist and accept their argument
    parser.handle_pi("x")
    parser.handle_entityref("amp")
    parser.handle_charref("9731")
    parser.unknown_decl("CDATA[x]")


def test_reset_clears_buffer_and_position() -> None:
    parser = _Recorder()
    parser.feed("<div incomplete")  # an unfinished tag stays buffered, dispatching nothing
    parser.reset()
    assert parser.getpos() == (1, 0)
    assert parser.events == []
    parser.feed("<p>x</p>")
    parser.close()
    assert parser.events == [("start", "p", []), ("data", "x"), ("end", "p")]


def test_getpos_tracks_the_last_token() -> None:
    parser = _Recorder()
    parser.feed("<p>\n<a>")
    line, col = parser.getpos()
    assert line == 2
    assert col >= 0


def test_convert_charrefs_argument_is_accepted_but_inert() -> None:
    # the argument is kept for signature compatibility; references decode either way
    parser = _Recorder(convert_charrefs=False)
    assert parser.convert_charrefs is False
    parser.feed("a &amp; b")
    parser.close()
    assert parser.events == [("data", "a & b")]


def test_raw_text_element_content_is_not_markup() -> None:
    # a script's body is character data, so the dispatch has to switch the content model the way iteration does
    parser = _Recorder()
    parser.feed("<script>if (a < b) { x('<p>'); }</script>")
    parser.close()
    assert ("data", "if (a < b) { x('<p>'); }") in parser.events


def test_doctype_with_public_and_system_identifiers() -> None:
    parser = _Recorder()
    parser.feed('<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">')
    parser.close()
    assert (
        "decl",
        'DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd"',
    ) in parser.events


def test_doctype_with_system_identifier_only() -> None:
    parser = _Recorder()
    parser.feed('<!DOCTYPE html SYSTEM "about:legacy-compat">')
    parser.close()
    assert ("decl", 'DOCTYPE html SYSTEM "about:legacy-compat"') in parser.events


def test_doctype_without_a_name() -> None:
    parser = _Recorder()
    parser.feed("<!DOCTYPE>")
    parser.close()
    assert ("decl", "DOCTYPE") in parser.events


def test_handler_exception_reaches_the_caller() -> None:
    class Failing(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.seen: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            self.seen.append(tag)
            msg = f"handler said no to {tag} with {len(attrs)} attributes"
            raise ValueError(msg)

    parser = Failing()
    with pytest.raises(ValueError, match="handler said no to p"):
        parser.feed("<p>")
    assert parser.seen == ["p"]


def test_doctype_with_public_identifier_only() -> None:
    parser = _Recorder()
    parser.feed('<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN">')
    parser.close()
    assert ("decl", 'DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN"') in parser.events
