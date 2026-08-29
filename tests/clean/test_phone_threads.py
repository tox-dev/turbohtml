from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Final

from turbohtml.clean import LinkDetector, Linker, Linkify, PhoneNumbers

_WORKERS: Final = 8


def test_one_linker_and_one_detector_shared_across_threads() -> None:
    phones = PhoneNumbers(regions=("US", "GB"))
    linker = Linker(Linkify(phones=phones, parse_email=True))
    detector = LinkDetector(phones=phones)
    barrier = Barrier(_WORKERS)
    text = "mail a@b.com, call 650-253-0000 or +44 20 7946 0958 x12, see example.com"

    def work(_index: int) -> tuple[str, list[str]]:
        barrier.wait()
        return linker.linkify(text), [span.url for span in detector.find(text)]

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        assert (
            list(pool.map(work, range(_WORKERS)))
            == [
                (
                    (
                        'mail <a href="mailto:a@b.com">a@b.com</a>, call '
                        '<a href="tel:+16502530000">650-253-0000</a> or '
                        '<a href="tel:+442079460958;ext=12">+44 20 7946 0958 x12</a>, '
                        'see <a href="http://example.com" rel="nofollow">example.com</a>'
                    ),
                    ["mailto:a@b.com", "tel:+16502530000", "tel:+442079460958;ext=12", "http://example.com"],
                )
            ]
            * _WORKERS
        )


def test_detectors_with_different_policies_keep_their_own_answers() -> None:
    phones = PhoneNumbers(regions=("US",))
    domains = LinkDetector(phones=phones, tlds=["corp"])
    numbers = LinkDetector(phones=phones, bare_domains=False, emails=False)
    barrier = Barrier(_WORKERS)
    text = "6502530000.corp 6502530000@example.com"

    def work(index: int) -> list[str]:
        barrier.wait()
        return [span.url for span in (domains if index % 2 == 0 else numbers).find(text)]

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        assert list(pool.map(work, range(_WORKERS))) == [
            ["http://6502530000.corp", "mailto:6502530000@example.com"],
            ["tel:+16502530000"],
        ] * (_WORKERS // 2)
