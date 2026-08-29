from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml.clean import LinkCandidate, LinkDetector, Linker, Linkify, PhoneNumber, PhoneNumbers, PhoneType, linkify

if TYPE_CHECKING:
    from collections.abc import Callable

_US: Final = PhoneNumbers(regions=("US",))


def test_acceptance_case() -> None:
    assert linkify("Call 650-253-0000", Linkify(phones=_US)) == 'Call <a href="tel:+16502530000">650-253-0000</a>'
    assert LinkDetector(phones=_US).find("Call 650-253-0000")[0].phone is not None


def test_a_reused_linker_and_one_without_phones() -> None:
    with_phones = Linker(Linkify(phones=_US))
    plain = Linker()
    for _ in range(2):
        assert with_phones.linkify("650-253-0000") == '<a href="tel:+16502530000">650-253-0000</a>'
        assert plain.linkify("650-253-0000") == "650-253-0000"


@pytest.mark.parametrize(
    ("text", "regions", "href"),
    [
        pytest.param("650-253-0000", ("US",), "tel:+16502530000", id="national"),
        pytest.param("011 44 20 7946 0958", ("US",), "tel:+442079460958", id="idd"),
        pytest.param(
            "\uff0b\uff14\uff14 \uff12\uff10 \uff17\uff19\uff14\uff16 \uff10\uff19\uff15\uff18",
            (),
            "tel:+442079460958",
            id="fullwidth",
        ),
        pytest.param("06 12345678", ("IT",), "tel:+390612345678", id="leading-zero-kept"),
        pytest.param("+800 1234 5678", (), "tel:+80012345678", id="non-geographic"),
        pytest.param("+49 800 1234567890", (), "tel:+498001234567890", id="over-fifteen-digits"),
        pytest.param("650-253-0000 ext. 1234", ("US",), "tel:+16502530000;ext=1234", id="extension"),
        pytest.param("650-253-0000 x1", ("US",), "tel:+16502530000;ext=1", id="one-digit-extension"),
    ],
)
def test_href_is_the_international_number(text: str, regions: tuple[str, ...], href: str) -> None:
    assert linkify(text, Linkify(phones=PhoneNumbers(regions=regions))) == f'<a href="{href}">{text}</a>'
    span = LinkDetector(phones=PhoneNumbers(regions=regions)).find(text)[0]
    assert span.phone is not None
    expected = "tel:" + span.phone.international_number
    if span.phone.extension is not None:
        expected += ";ext=" + span.phone.extension
    assert href == expected


def _collect(seen: list[LinkCandidate]) -> Callable[[LinkCandidate], LinkCandidate]:
    def callback(link: LinkCandidate) -> LinkCandidate:
        seen.append(link)
        return link

    return callback


@pytest.mark.parametrize("entry", [pytest.param("function", id="linkify"), pytest.param("linker", id="Linker")])
def test_callback_sees_the_phone(entry: str) -> None:
    seen: list[LinkCandidate] = []
    config = Linkify(callbacks=[_collect(seen)], phones=_US, parse_email=True)
    text = "mail a@b.com, see example.com, call 650-253-0000"
    assert (linkify(text, config) if entry == "function" else Linker(config).linkify(text)) == (
        'mail <a href="mailto:a@b.com">a@b.com</a>, see <a href="http://example.com">example.com</a>, '
        'call <a href="tel:+16502530000">650-253-0000</a>'
    )
    assert [link.phone for link in seen] == [
        None,
        None,
        PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
    ]


def test_callback_can_route_mobiles_to_sms() -> None:
    def sms(link: LinkCandidate) -> LinkCandidate:
        if link.phone is not None and link.phone.type in {PhoneType.MOBILE, PhoneType.FIXED_LINE_OR_MOBILE}:
            link.url = "sms:" + link.phone.international_number
        return link

    assert linkify(
        "a@b.com or 07400 123456 or 020 7946 0958",
        Linkify(callbacks=[sms], phones=PhoneNumbers(regions=("GB",)), parse_email=True),
    ) == (
        '<a href="mailto:a@b.com">a@b.com</a> or <a href="sms:+447400123456">07400 123456</a> or '
        '<a href="tel:+442079460958">020 7946 0958</a>'
    )


def test_veto_leaves_the_text_bare() -> None:
    assert linkify("call 650-253-0000", Linkify(callbacks=[lambda _link: None], phones=_US)) == "call 650-253-0000"


def test_skip_tags_and_existing_anchors_are_untouched() -> None:
    assert linkify(
        '<code>650-253-0000</code> <a href="/x">650-253-0000</a> <script>650-253-0000</script> 650-253-0000',
        Linkify(phones=_US, skip_tags=["code"]),
    ) == (
        '<code>650-253-0000</code> <a href="/x">650-253-0000</a> <script>650-253-0000</script> '
        '<a href="tel:+16502530000">650-253-0000</a>'
    )


def test_a_number_split_across_elements_is_not_joined() -> None:
    assert linkify("<b>650-253</b>-0000", Linkify(phones=_US)) == "<b>650-253</b>-0000"


def test_nbsp_inside_a_number() -> None:
    assert linkify("650&nbsp;253&nbsp;0000", Linkify(phones=_US)) == (
        '<a href="tel:+16502530000">650&nbsp;253&nbsp;0000</a>'
    )


def test_written_tel_uri_links_to_its_number() -> None:
    assert linkify("tel:+1-650-253-0000", Linkify(phones=_US)) == '<a href="tel:+16502530000">tel:+1-650-253-0000</a>'


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<a href="https://x.org">650-253-0000</a>', id="web-link"),
        pytest.param('<a href="tel:+16502530000;ext=12">650-253-0000 x12</a>', id="tel-link"),
        pytest.param('<a href="tel:not-a-number">call</a>', id="malformed-tel-link"),
    ],
)
def test_existing_anchors_reach_the_callback_without_a_phone(html: str) -> None:
    seen: list[LinkCandidate] = []
    assert linkify(html, Linkify(callbacks=[_collect(seen)], phones=_US, process_existing=True)) == html
    assert [(link.existing, link.phone) for link in seen] == [(True, None)]


def test_candidate_phone_defaults_to_none() -> None:
    assert LinkCandidate("http://x", "x").phone is None
    assert LinkCandidate("tel:+1", "1", phone=None).phone is None
