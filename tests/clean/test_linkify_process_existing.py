from __future__ import annotations

from typing import TYPE_CHECKING

from turbohtml.clean import LinkCandidate, Linkify, linkify, nofollow, target_blank

if TYPE_CHECKING:
    from turbohtml.clean import Callback


def _no_callbacks() -> list[Callback]:
    return []


def test_link_existing_defaults_to_false() -> None:
    assert LinkCandidate("http://x.com", "x").existing is False


def test_link_existing_can_be_set() -> None:
    assert LinkCandidate("http://x.com", "x", existing=True).existing is True


def test_existing_anchor_untouched_without_flag() -> None:
    html = '<a href="http://x.com">x</a>'
    assert linkify(html, Linkify(callbacks=[nofollow])) == html


def test_process_existing_runs_callbacks_over_anchor() -> None:
    html = '<a href="http://x.com">x</a>'
    assert linkify(html, Linkify(process_existing=True)) == '<a href="http://x.com" rel="nofollow">x</a>'


def test_process_existing_veto_unwraps_anchor() -> None:
    html = '<a href="http://x.com">click</a>'
    assert linkify(html, Linkify(callbacks=[lambda _link: None], process_existing=True)) == "click"


def test_process_existing_can_change_text() -> None:
    def relabel(link: LinkCandidate) -> LinkCandidate:
        link.text = "link"
        return link

    html = '<a href="http://x.com">original</a>'
    assert linkify(html, Linkify(callbacks=[relabel], process_existing=True)) == '<a href="http://x.com">link</a>'


def test_process_existing_keeps_inner_markup_when_text_unchanged() -> None:
    def add_rel(link: LinkCandidate) -> LinkCandidate:
        link.attrs["rel"] = "ext"
        return link

    html = '<a href="http://x.com"><b>x</b></a>'
    out = linkify(html, Linkify(callbacks=[add_rel], process_existing=True))
    assert out == '<a href="http://x.com" rel="ext"><b>x</b></a>'


def test_process_existing_callback_can_remove_attr() -> None:
    html = '<a href="mailto:a@b.com" target="_blank">mail</a>'
    out = linkify(html, Linkify(callbacks=[target_blank], process_existing=True))
    assert out == '<a href="mailto:a@b.com">mail</a>'


def test_process_existing_preserves_token_list_attr() -> None:
    html = '<a href="http://x.com" class="a b">x</a>'
    out = linkify(html, Linkify(callbacks=_no_callbacks(), process_existing=True))
    assert out == '<a href="http://x.com" class="a b">x</a>'


def test_process_existing_flattens_valueless_attr() -> None:
    html = "<a download>x</a>"
    out = linkify(html, Linkify(callbacks=_no_callbacks(), process_existing=True))
    assert out == '<a download="">x</a>'


def test_process_existing_anchor_without_href_stays_bare() -> None:
    html = "<a>plain</a>"
    assert linkify(html, Linkify(callbacks=[nofollow], process_existing=True)) == "<a>plain</a>"


def test_existing_flag_distinguishes_existing_from_detected() -> None:
    def mark(link: LinkCandidate) -> LinkCandidate:
        link.attrs["data-kind"] = "existing" if link.existing else "new"
        return link

    html = '<a href="http://x.com">x</a> see http://y.com'
    out = linkify(html, Linkify(callbacks=[mark], process_existing=True))
    assert '<a href="http://x.com" data-kind="existing">x</a>' in out
    assert 'data-kind="new"' in out


def test_process_existing_still_detects_links_in_other_tags() -> None:
    html = "<p>see http://x.com</p>"
    out = linkify(html, Linkify(callbacks=_no_callbacks(), process_existing=True))
    assert out == '<p>see <a href="http://x.com">http://x.com</a></p>'


def test_process_existing_leaves_anchor_in_skip_tag_alone() -> None:
    html = '<code><a href="http://x.com">x</a></code>'
    assert linkify(html, Linkify(callbacks=[nofollow], skip_tags=["code"], process_existing=True)) == html


def test_how_to_doctest_scenario() -> None:
    def annotate(link: LinkCandidate) -> LinkCandidate:
        link.attrs["data-seen"] = "author" if link.existing else "auto"
        return link

    html = '<a href="https://docs.example">docs</a>, ping app.internal, skip ftp://x.example'
    out = linkify(
        html, Linkify(callbacks=[annotate], process_existing=True, extra_tlds=["internal"], schemes=["https"])
    )
    assert out == (
        '<a href="https://docs.example" data-seen="author">docs</a>, '
        'ping <a href="http://app.internal" data-seen="auto">app.internal</a>, skip ftp://x.example'
    )
