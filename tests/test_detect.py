from __future__ import annotations

import hashlib
import importlib
import random
import string
from importlib.util import find_spec
from itertools import islice, product
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.operations import INPUTS

from turbohtml import parse
from turbohtml.detect import (
    Detection,
    EncodingDetector,
    EncodingMatch,
    LanguageDetection,
    LanguageMatch,
    detect,
    detect_all,
    detect_language,
)

if TYPE_CHECKING:
    from types import ModuleType

_RUSSIAN_1251: Final[bytes] = "Программирование помогает понять структуру вычислительных систем сегодня здесь.".encode(
    "cp1251"
)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param(
            b"", EncodingMatch(encoding=None, confidence=0.0, language=None), id="empty-input-has-no-encoding"
        ),
        pytest.param(
            b"<p>hello world</p>",
            EncodingMatch(encoding="windows-1252", confidence=1.0, language=None, codec="whatwg-windows-1252"),
            id="pure-ascii-is-certain",
        ),
        pytest.param(
            b"<meta charset=iso-8859-2><p>x</p>",
            EncodingMatch(encoding="ISO-8859-2", confidence=1.0, language=None, bom=False, codec="whatwg-iso-8859-2"),
            id="meta-prescan-is-certain-without-a-bom",
        ),
        pytest.param(
            "café résumé Москва 日本語".encode(),
            EncodingMatch(encoding="UTF-8", confidence=1.0, language=None, codec="whatwg-utf-8"),
            id="valid-utf8-is-certain",
        ),
        # 0x81 followed by a newline disqualifies every candidate, so the WHATWG windows-1252 default
        # is returned with confidence 0.0: there is no positive evidence for it
        pytest.param(
            b"\x81\n",
            EncodingMatch(encoding="windows-1252", confidence=0.0, language=None, codec="whatwg-windows-1252"),
            id="undecodable-bytes-fall-back-to-windows-1252-with-no-confidence",
        ),
    ],
)
def test_detect_bytes(data: bytes, expected: EncodingMatch) -> None:
    assert detect(data) == expected


@pytest.mark.parametrize(
    ("raw", "encoding"),
    [
        pytest.param(b"\xef\xbb\xbfhello", "UTF-8-SIG", id="utf-8-sig"),
        pytest.param(b"\xff\xfeh\x00", "UTF-16LE", id="utf-16le"),
        pytest.param(b"\xfe\xff\x00h", "UTF-16BE", id="utf-16be"),
        pytest.param(b"\xff\xfe", "UTF-16LE", id="utf-16le-bare-mark"),
        pytest.param(b"\xff\xfe\x00\x00", "UTF-16LE", id="ff-fe-00-00-is-utf-16le-not-utf-32le"),
        pytest.param(b"\xff\xfe\x01\x00", "UTF-16LE", id="ff-fe-01-00-is-utf-16le"),
    ],
)
def test_byte_order_mark_reports_its_label_and_flag(raw: bytes, encoding: str) -> None:
    # a mark identifies the encoding unambiguously: UTF-8 reports UTF-8-SIG so a caller can strip it,
    # and the UTF-16 marks report their exact label; every marked result carries bom=True
    assert detect(raw) == EncodingMatch(
        encoding=encoding, confidence=1.0, language=None, bom=True, codec=f"whatwg-{encoding.casefold()}"
    )


def test_utf_8_sig_is_the_only_bom_label_that_differs_from_parse() -> None:
    # the standalone detector spells a UTF-8 mark UTF-8-SIG so a caller can strip it; parse() keeps the spec's UTF-8
    raw: Final[bytes] = b"\xef\xbb\xbfhello"
    assert (detect(raw).encoding, parse(raw, detect_encoding=True).encoding) == ("UTF-8-SIG", "UTF-8")


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"\xff\xfe\x00\x00h\x00\x00\x00", id="ff-fe-00-00-the-utf-32le-byte-pattern"),
        pytest.param(b"\x00\x00\xfe\xff\x00\x00\x00h", id="00-00-fe-ff-the-utf-32be-byte-pattern"),
    ],
)
def test_detect_and_parse_agree_on_a_utf_32_bom(raw: bytes) -> None:
    # the WHATWG BOM sniff recognizes no UTF-32, so neither path may report a UTF-32 label
    assert detect(raw).encoding == parse(raw, detect_encoding=True).encoding


def test_iso_2022_jp_is_certain() -> None:
    raw = "こんにちは世界".encode("iso-2022-jp")
    assert detect(raw) == EncodingMatch(
        encoding="ISO-2022-JP", confidence=1.0, language="Japanese", codec="whatwg-iso-2022-jp"
    )


@pytest.mark.parametrize(
    ("text", "source", "encoding", "language"),
    [
        pytest.param(
            "Précédemment, la créativité française était très développée près de Paris ici.",
            "cp1252",
            "windows-1252",
            None,
            id="french-1252",
        ),
        pytest.param(
            "Программирование помогает понять структуру вычислительных систем сегодня здесь.",
            "cp1251",
            "windows-1251",
            "Russian",
            id="russian-1251",
        ),
        pytest.param(
            "Москва это столица России и очень большой красивый город здесь сейчас опять.",
            "koi8-r",
            "KOI8-U",
            "Russian",
            id="russian-koi8",
        ),
        pytest.param(
            "Η ελληνική γλώσσα είναι μία από τις αρχαιότερες γλώσσες στον κόσμο σήμερα εδώ.",  # ruff:ignore[ambiguous-unicode-character-string]  # non-Latin sample text is the input under test
            "cp1253",
            "windows-1253",
            "Greek",
            id="greek-1253",
        ),
        pytest.param(
            "اللغة العربية لغة جميلة وغنية بالكلمات والتعابير المختلفة في العالم العربي كله.",
            "cp1256",
            "windows-1256",
            "Arabic",
            id="arabic-1256",
        ),
        pytest.param(
            "日本語のテキストをここに書きます。今日はとても良い天気ですね。",
            "shift_jis",
            "Shift_JIS",
            "Japanese",
            id="japanese-shift_jis",
        ),
        pytest.param(
            "한국어 텍스트를 여기에 작성합니다 오늘은 날씨가 정말 좋습니다 그렇죠.",
            "euc_kr",
            "EUC-KR",
            "Korean",
            id="korean-euc-kr",
        ),
        pytest.param(
            "这是一段简体中文文本用来测试编码检测今天天气非常好我们去公园散步吧。",
            "gbk",
            "GBK",
            "Chinese",
            id="simplified-gbk",
        ),
    ],
)
def test_scored_detection_recovers_encoding_and_language(text: str, source: str, encoding: str, language: str) -> None:
    match = detect(text.encode(source))
    assert (match.encoding, match.language) == (encoding, language)
    assert 0.0 < match.confidence <= 1.0


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("café résumé".encode(), id="utf-8"),
        pytest.param("Программирование помогает всем".encode("cp1251"), id="windows-1251"),
        pytest.param(b"<meta charset=iso-8859-2><p>\xe1</p>", id="meta-prescan"),
        pytest.param(b"\x81\n", id="fallback"),
    ],
)
def test_agrees_with_parse_detect_encoding(raw: bytes) -> None:
    assert detect(raw).encoding == parse(raw, detect_encoding=True).encoding


def test_detect_all_on_a_bom_is_a_single_marked_match() -> None:
    # a mark is certain, so it collapses the ranking to one entry that carries the bom flag
    assert detect_all(b"\xef\xbb\xbfhello") == [
        EncodingMatch(encoding="UTF-8-SIG", confidence=1.0, language=None, bom=True, codec="whatwg-utf-8-sig")
    ]


def test_scored_result_is_not_marked() -> None:
    assert detect(_RUSSIAN_1251).bom is False


def test_detect_all_leads_with_the_detect_winner() -> None:
    matches = detect_all(_RUSSIAN_1251)
    assert matches[0] == detect(_RUSSIAN_1251)


def test_detect_all_ranks_alternatives_by_confidence() -> None:
    alternatives = detect_all(_RUSSIAN_1251)[1:]
    assert alternatives
    assert [match.confidence for match in alternatives] == sorted(
        (match.confidence for match in alternatives), reverse=True
    )


def test_detect_all_confidences_share_a_unit_budget() -> None:
    assert sum(match.confidence for match in detect_all(_RUSSIAN_1251)) == pytest.approx(1.0)


def test_detect_all_reports_each_encoding_once() -> None:
    # two candidates share the windows-1252 model (one per language family); only the better one is reported
    encodings = [match.encoding for match in detect_all(_RUSSIAN_1251)]
    assert encodings.count("windows-1252") == 1


def test_hebrew_visual_tiebreak_leads_the_ranking() -> None:
    # right-to-left (visual-order) Hebrew ties windows-1255 on score; the engine's punctuation tiebreak
    # picks ISO-8859-8 and the ranking keeps that winner first even though the tie sorts windows-1255 earlier
    matches = detect_all("שלום לכולם זהו משפט בעברית עם סימני פיסוק רבים, נקודות. ועוד!"[::-1].encode("iso-8859-8"))
    assert matches[0].encoding == "ISO-8859-8"
    assert matches[1].encoding == "windows-1255"
    assert matches[0].confidence == matches[1].confidence


def test_non_bytes_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        detect("text")  # ty: ignore[invalid-argument-type]  # str exposes no byte buffer


# Content-based language detection (roadmap #459) scores a character-trigram rank model: the samples below detect their
# language exactly, and the detector is validated bit-for-bit against whatlang's own Method::Trigram on all 69 entries
# of its example corpus, confidence to 1e-6 included.
_LANGUAGE_CASES: Final[tuple[tuple[str, str, str], ...]] = (
    ("There is no reason not to learn a new language every single year of your life.", "eng", "Latin"),
    ("Die Ordnung muss für immer in diesem Codebase bleiben und zuverlässig funktionieren.", "deu", "Latin"),
    ("Además de todo lo anteriormente dicho, también encontramos que resulta muy útil.", "spa", "Latin"),
    (
        "Aujourd'hui nous allons apprendre comment préparer un délicieux gâteau au chocolat pour la fête.",
        "fra",
        "Latin",
    ),
    ("Além de tudo o que foi mencionado antes, encontramos também algo muito interessante hoje.", "por", "Latin"),
    ("Programmering hjælper med at forstå strukturen af moderne beregningssystemer i dag.", "dan", "Latin"),
    ("Programmering hjelper oss med å forstå strukturen til beregningssystemer bedre nå.", "nob", "Latin"),
    ("Программирование помогает понять структуру вычислительных систем сегодня здесь.", "rus", "Cyrillic"),
    ("Та нічого, все нормально. А в тебе як справи сьогодні зранку, дорогий друже мій?", "ukr", "Cyrillic"),  # ruff:ignore[ambiguous-unicode-character-string]  # non-Latin sample text is the input under test
    ("Η γλώσσα μας είναι πολύ όμορφη και έχει πλούσια και μεγάλη ιστορία στον κόσμο.", "ell", "Greek"),  # ruff:ignore[ambiguous-unicode-character-string]  # non-Latin sample text is the input under test
    ("האקדמיה ללשון העברית היא המוסד העליון למדע הלשון העברית שנמצא היום בירושלים.", "heb", "Hebrew"),
    ("ككل حوالي ومعظم الناس يتحدثون هذه اللغة الجميلة في جميع أنحاء العالم اليوم بسعادة.", "ara", "Arabic"),
    ("हिमालयी वन में रहने वाली यह चिड़िया बहुत सुंदर होती है और यहाँ बहुत आम पाई जाती है।", "hin", "Devanagari"),
    ("北京是中国的首都也是全国的政治文化中心而且历史非常悠久经济发展十分迅速。", "cmn", "Mandarin"),
    ("これは日本語で書かれたテキストです。ひらがなとカタカナと漢字を一緒に使います。", "jpn", "Hiragana"),
    ("한국어는 매우 아름다운 언어이며 배우기 쉽고 아주 재미있는 언어입니다 정말로요.", "kor", "Hangul"),
    ("ภาษาไทยเป็นภาษาที่สวยงามและมีประวัติศาสตร์อันยาวนานมากในภูมิภาคนี้ครับ", "tha", "Thai"),
    ("ქართული ენა არის ერთ-ერთი უძველესი ენა მსოფლიოში და მას აქვს საკუთარი ანბანი.", "kat", "Georgian"),
)


@pytest.mark.parametrize(("text", "language", "script"), [pytest.param(*case, id=case[1]) for case in _LANGUAGE_CASES])
def test_detect_language_names_the_language_and_script(text: str, language: str, script: str) -> None:
    match = detect_language(text)
    assert (match.language, match.script) == (language, script)
    # confidence is at least 0.9; only Norwegian dips below 1.0, tied close to its neighbor Danish
    assert match.confidence >= 0.9


def test_detect_language_accuracy_over_the_sample_set() -> None:
    # every representative sample resolves to its language: 18/18 across nine scripts
    correct = sum(detect_language(text).language == language for text, language, _script in _LANGUAGE_CASES)
    assert correct == len(_LANGUAGE_CASES)


def test_detect_language_reports_the_english_name() -> None:
    assert detect_language("Die Ordnung muss für immer bleiben und gut funktionieren heute.").name == "German"


@pytest.mark.parametrize(
    "text",
    [pytest.param("", id="empty"), pytest.param("1234567890 !@#$%^&*() []{}|~ +=<>?", id="symbols-and-digits")],
)
def test_text_without_a_script_has_no_language(text: str) -> None:
    # nothing that carries a script survives, so there is no language and no script to report
    assert detect_language(text) == LanguageMatch(language=None, confidence=0.0, script=None)


def test_short_input_is_low_confidence() -> None:
    # two short words cannot separate the Latin languages, so the winner comes back well under full confidence
    match = detect_language("por que")
    assert match.language is not None
    assert match.confidence < 0.1


def test_threshold_drops_a_low_confidence_result() -> None:
    assert detect_language("por que", LanguageDetection(threshold=0.5)) == LanguageMatch(
        language=None, confidence=0.0, script=None
    )


def test_threshold_keeps_a_confident_result() -> None:
    match = detect_language("There is no reason not to learn a language today.", LanguageDetection(threshold=0.5))
    assert match.language == "eng"


def test_allowed_constrains_the_candidates() -> None:
    # Esperanto shares the Latin script with Ukrainian's Cyrillic; only Esperanto is a Latin candidate, so it wins
    assert detect_language("Mi ne scias!", LanguageDetection(allowed=frozenset({"epo", "ukr"}))).language == "epo"


def test_allowed_with_a_single_candidate_is_certain() -> None:
    # one surviving candidate has no runner-up to compare against, so its confidence is 1.0
    match = detect_language("Mi ne scias hodiaŭ!", LanguageDetection(allowed=frozenset({"epo"})))
    assert match == LanguageMatch(language="epo", confidence=1.0, script="Latin", name="Esperanto")


def test_excluded_removes_a_language() -> None:
    match = detect_language(
        "I am begging pardon",
        LanguageDetection(excluded=frozenset({"jav", "nld", "uzb", "swe", "nob", "tgl", "cym"})),
    )
    assert match.language == "eng"


def test_excluding_every_language_of_a_script_yields_no_match() -> None:
    # Hebrew and Yiddish are the only Hebrew-script languages; excluding both leaves nothing to return
    assert (
        detect_language("האקדמיה ללשון העברית", LanguageDetection(excluded=frozenset({"heb", "yid"}))).language is None
    )


def test_excluded_applies_to_a_single_language_script() -> None:
    # a single-language script still honors the constraint: excluding Greek leaves the Greek text unresolved
    text = "Η γλώσσα μας είναι όμορφη"  # ruff:ignore[ambiguous-unicode-character-string]
    assert detect_language(text, LanguageDetection(excluded=frozenset({"ell"}))).language is None


@pytest.mark.parametrize(
    ("suffix", "language", "confidence"),
    [
        pytest.param("", "cmn", 1.0, id="pure-han-is-chinese"),
        pytest.param("の", "cmn", 0.5, id="a-little-kana-is-uncertain-chinese"),
        pytest.param("のかさ", "jpn", 0.5, id="some-kana-is-uncertain-japanese"),
        pytest.param("のかさたなはまやらわ", "jpn", 1.0, id="much-kana-is-japanese"),
    ],
)
def test_han_text_splits_between_chinese_and_japanese_by_kana(suffix: str, language: str, confidence: float) -> None:
    match = detect_language(f"北京是中国首都也是政治文化中心历史悠久发展迅速人口众多经济繁荣科技教育{suffix}")
    assert (match.language, match.confidence) == (language, pytest.approx(confidence))


@pytest.mark.parametrize(
    ("options", "language"),
    [
        pytest.param(LanguageDetection(allowed=frozenset({"jpn"})), "jpn", id="allow-only-japanese"),
        pytest.param(LanguageDetection(allowed=frozenset({"cmn"})), "cmn", id="allow-only-chinese"),
        pytest.param(LanguageDetection(excluded=frozenset({"cmn", "jpn"})), None, id="exclude-both"),
    ],
)
def test_han_text_honors_the_language_filter(options: LanguageDetection, language: str | None) -> None:
    assert detect_language("北京是中国的首都", options).language == language


def test_gibberish_in_a_shared_script_has_zero_confidence() -> None:
    # no language profile contains these trigrams, so the best score is zero and confidence collapses to 0.0
    assert detect_language("qxqx").confidence == pytest.approx(0.0, abs=1e-9)


def test_a_single_language_glyph_run_scores_its_lone_match() -> None:
    # only Esperanto profiles carry the u-breve trigrams; every other candidate scores zero, so the confidence
    # is Esperanto's own similarity rather than a comparison against a runner-up
    match = detect_language("ŭaŭ")
    assert match.language == "epo"
    assert 0.0 < match.confidence < 1.0


def test_unclassified_characters_do_not_derail_a_verdict() -> None:
    # an emoji and a mathematical symbol belong to no script; they are ignored, and the German text still wins
    assert detect_language("Die Ordnung muss ∀ 😀 für immer bleiben und gut funktionieren heute.").language == "deu"


def test_a_long_passage_stays_confident() -> None:
    passage = (
        "The history of computing hardware covers the developments from early mechanical calculating devices to "
        "modern electronic computers, spanning many centuries of gradual refinement and sudden revolutionary leaps. "
        "Ancient civilisations built tally sticks, abacuses, and astronomical instruments long before anyone "
        "imagined a programmable machine capable of arbitrary logic and endless tireless repetition without fatigue. "
        "During the nineteenth century, inventors sketched elaborate mechanical engines driven by cranks, gears, "
        "punched cards, and steam, yet most remained unfinished dreams scattered carelessly across dusty notebooks. "
        "The twentieth century finally delivered vacuum tubes, transistors, integrated circuits, and eventually "
        "microprocessors, shrinking room sized behemoths down into pocket companions cheaper than anybody promised. "
        "Today a wristwatch outperforms the machines that once guided astronauts safely toward the distant moon, "
        "and tomorrow's designs promise capabilities their earnest inventors can scarcely begin to describe aloud."
    )
    assert detect_language(passage) == LanguageMatch(language="eng", confidence=1.0, script="Latin", name="English")


@pytest.mark.parametrize(
    ("word_count", "expected"),
    [
        pytest.param(128, LanguageMatch(language="cym", confidence=1.0, script="Latin", name="Welsh"), id="below-cap"),
        pytest.param(
            1024,
            LanguageMatch(language="aka", confidence=0.1691861256901001, script="Latin", name="Akan"),
            id="above-cap",
        ),
        pytest.param(4096, LanguageMatch(language="cym", confidence=1.0, script="Latin", name="Welsh"), id="many-ties"),
    ],
)
@pytest.mark.parametrize("step", [pytest.param(1, id="forward"), pytest.param(-1, id="reverse")])
def test_language_trigram_ties_ignore_word_order(word_count: int, expected: LanguageMatch, *, step: int) -> None:
    words: Final[tuple[str, ...]] = tuple(
        "".join(word) for word in islice(product(string.ascii_lowercase, repeat=3), word_count)
    )
    assert detect_language("  ".join(words[::step])) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "  ".join("".join(word) for word in product(string.ascii_lowercase, repeat=3)),
            LanguageMatch(language="sna", confidence=0.1332846056280532, script="Latin", name="Shona"),
            id="distinct-trigrams",
        ),
        pytest.param(
            "  ".join("".join(word) for word in product("abcde", repeat=3)),
            LanguageMatch(language="cym", confidence=1.0, script="Latin", name="Welsh"),
            id="few-trigrams",
        ),
        pytest.param(
            "the quick brown fox jumps over the lazy dog. " * 2048,
            LanguageMatch(language="eng", confidence=1.0, script="Latin", name="English"),
            id="repeated-trigrams",
        ),
    ],
)
def test_language_trigram_ranking(text: str, expected: LanguageMatch) -> None:
    assert detect_language(text) == expected


def test_distance_saturates_on_an_adversarial_text() -> None:
    # a long block of high-frequency nonsense trigrams pushes the real-language trigrams appended after it to
    # the tail of the frequency ranking, so their rank displacement drives one candidate's distance past the
    # 90000 ceiling and exercises the saturation clamp; the detector still returns a Latin verdict without error
    filler = " ".join("".join(word) for word in islice(product("bcdfghjklmnpqrstvwxz", repeat=4), 300))
    tail = "Đây là một đoạn văn bản tiếng Việt với nhiều dấu thanh khác nhau được viết ra hôm nay"
    match = detect_language(f"{(f'{filler} ') * 6}{tail}")
    assert match.script == "Latin"


def test_non_string_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        detect_language(b"bytes")  # ty: ignore[invalid-argument-type]  # the detector requires text, not bytes


@pytest.mark.parametrize("threshold", [pytest.param(1.5, id="above-one"), pytest.param(-0.1, id="below-zero")])
def test_language_detection_rejects_an_out_of_range_threshold(threshold: float) -> None:
    with pytest.raises(ValueError, match="threshold must be within"):
        LanguageDetection(threshold=threshold)


def test_language_detection_rejects_allowed_with_excluded() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        LanguageDetection(allowed=frozenset({"eng"}), excluded=frozenset({"deu"}))


@pytest.mark.parametrize(
    "payload",
    [
        # each shape lands the prescan on a "<" whose following bytes exercise one dispatch arm the memchr jump
        # otherwise skips past: a meta opened too close to the end, a meta closed by "/", a "</" with no room for
        # its letter, and a bare "</" bogus comment
        pytest.param(b"x" * 40 + b"<meta", id="meta-at-buffer-end"),
        pytest.param(b'<meta/charset="windows-1250">\xe1', id="meta-self-closing-name"),
        pytest.param(b"y" * 40 + b"</", id="closing-tag-at-buffer-end"),
        pytest.param(b"<//not-a-tag><p>\xe1</p>", id="bogus-double-slash"),
        pytest.param(b"<meta ><meta charset=windows-1251>\xd0", id="meta-space-no-attrs"),
    ],
)
def test_prescan_dispatch_edges(payload: bytes) -> None:
    # the answer only has to be a real decision; the point is that the prescan walks the shape without misreading it
    assert detect(payload).encoding


@pytest.mark.parametrize(
    ("chunks", "settled"),
    [
        pytest.param([b"\xef\xbb\xbfa"], True, id="utf-8"),
        pytest.param([b"\xfe\xff"], True, id="utf-16be"),
        pytest.param([b"\xff\xfe"], True, id="utf-16le-settles-on-its-two-bytes"),
        pytest.param([b"\xff\xfe\x00\x00"], True, id="ff-fe-00-00-settles-as-utf-16le"),
        pytest.param([b"\x00\x00\xfe\xff"], False, id="00-00-fe-ff-is-no-mark-not-utf-32be"),
        pytest.param([b"\xef\xbb", b"\xbfa"], True, id="a-mark-split-across-feeds-settles"),
        pytest.param([b"\xef\xbb"], False, id="a-truncated-mark"),
        pytest.param([b"hello"], False, id="no-mark"),
        pytest.param([b"\xff\xffab"], False, id="ff-then-not-fe"),
        pytest.param([b"\xef\xbb\xbe"], False, id="a-mark-that-differs-in-its-last-byte"),
        pytest.param([b""], False, id="an-empty-chunk"),
    ],
)
def test_feed_reports_whether_a_mark_settled_the_stream(chunks: list[bytes], *, settled: bool) -> None:
    detector: Final[EncodingDetector] = EncodingDetector()
    for chunk in chunks:
        detector.feed(chunk)
    assert detector.done is settled


@pytest.mark.parametrize(
    ("data", "encoding", "codec"),
    [
        pytest.param(b'<meta charset="x-mac-cyrillic">\xd0', "x-mac-cyrillic", "whatwg-x-mac-cyrillic", id="mac"),
        pytest.param(b'<meta charset="hz-gb-2312">x', "replacement", "whatwg-replacement", id="replacement"),
        pytest.param(b'<meta charset="big5">\x87\x40', "Big5", "whatwg-big5", id="big5"),
        pytest.param(b'<meta charset="shift_jis">\x80', "Shift_JIS", "whatwg-shift_jis", id="shift_jis"),
        pytest.param(b"\xef\xbb\xbfhi", "UTF-8-SIG", "whatwg-utf-8-sig", id="utf-8-bom"),
        pytest.param(b"\xff\xfeh\x00", "UTF-16LE", "whatwg-utf-16le", id="utf-16le-bom"),
    ],
)
def test_codec_names_a_registered_decoder(data: bytes, encoding: str, codec: str) -> None:
    match = detect(data)
    assert (match.encoding, match.codec) == (encoding, codec)


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b'<meta charset="x-mac-cyrillic">\xd0', "\u2013", id="x-mac-cyrillic-has-no-cpython-codec"),
        pytest.param(b'<meta charset="hz-gb-2312">x', "\ufffd", id="replacement-has-no-cpython-codec"),
    ],
)
def test_the_whatwg_name_alone_cannot_be_decoded(data: bytes, text: str) -> None:
    match = detect(data)
    assert match.encoding is not None
    assert match.codec is not None
    with pytest.raises(LookupError):
        data.decode(match.encoding)
    assert data.decode(match.codec).endswith(text)  # the codec always can, and decodes as the parser does


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b'<meta charset="koi8-u">\xae', "ў", id="koi8-u-is-koi8-ru"),
        pytest.param(b'<meta charset="big5">\x87\x40', "䏰", id="big5-index-is-a-superset"),
        pytest.param(b'<meta charset="gbk">\x80', "€", id="gbk-euro"),
    ],
)
def test_decoding_through_codec_reproduces_what_the_parser_saw(data: bytes, text: str) -> None:
    match = detect(data)
    assert match.codec is not None
    assert data.decode(match.codec).endswith(text)


@pytest.mark.parametrize(
    ("data", "text"),
    [
        pytest.param(b"\xef\xbb\xbfhi", "hi", id="utf-8-sig-strips-the-mark"),
        pytest.param(b"\xff\xfeh\x00", "\ufeffh", id="utf-16le-keeps-the-mark"),
        pytest.param(b"\xef\xbb\xbfhi\xc3", "hi\ufffd", id="utf-8-sig-truncated"),
        pytest.param(b"\xef\xbb\xbfhi\xc3(", "hi\ufffd(", id="utf-8-sig-bad-continuation"),
        pytest.param(b"\xff\xfe" + "AB".encode("utf-16-le") + b"\x41", "\ufeffAB\ufffd", id="utf-16le-lone-byte"),
        pytest.param(b"\xfe\xff" + "AB".encode("utf-16-be") + b"\x41", "\ufeffAB\ufffd", id="utf-16be-lone-byte"),
    ],
)
def test_a_byte_order_mark_codec_delegates_to_cpython(data: bytes, text: str) -> None:
    # CPython's UTF-8 and UTF-16 decoders match the spec, so the whatwg-* name resolves to them, with "replace"
    # errors where the strict codec would raise UnicodeDecodeError
    assert (codec := detect(data).codec) is not None
    assert data.decode(codec) == text


def test_a_mark_stripping_codec_reproduces_the_parser_text_on_malformed_bytes() -> None:
    # utf-8-sig drops the mark, so its decode equals the parser's text byte for byte, replacement char and all
    data: Final[bytes] = b"\xef\xbb\xbfhi\xc3"
    assert (codec := detect(data).codec) is not None
    assert data.decode(codec) == parse(data).text == "hi\ufffd"


def test_a_whatwg_codec_refuses_to_encode() -> None:
    # the generated tables are decode-side only; encoding to a legacy charset is a separate spec algorithm
    with pytest.raises(UnicodeError, match="decodes only"):
        "x".encode("whatwg-big5")


def test_an_unknown_whatwg_codec_is_not_registered() -> None:
    # non-empty input: CPython answers b"".decode(anything) with "" before it ever resolves the codec
    with pytest.raises(LookupError):
        b"x".decode("whatwg-no-such-encoding")


def test_the_no_match_sentinel_has_no_codec() -> None:
    match = detect(b"")
    assert match.encoding is None
    assert match.codec is None


def test_pure_ascii_agrees_with_the_parser() -> None:
    # "ascii" is not an encoding the spec names; its label resolves to windows-1252, which decodes ASCII identically
    assert detect(b"plain ascii").encoding == "windows-1252"
    assert parse(b"plain ascii", detect_encoding=True).encoding == "windows-1252"


_DETECTOR_RUSSIAN: Final[bytes] = (
    "Программирование помогает понять структуру вычислительных систем сегодня здесь.".encode("cp1251")
)


def test_chunked_feeds_equal_a_one_shot_detect() -> None:
    detector = EncodingDetector()
    detector.feed(_DETECTOR_RUSSIAN[:7])
    detector.feed(_DETECTOR_RUSSIAN[7:])
    assert not detector.done
    assert detector.result is None
    assert detector.close() == detect(_DETECTOR_RUSSIAN)
    assert detector.done


def test_a_chunk_opening_on_an_ascii_run_equals_a_one_shot_detect() -> None:
    # only a later chunk reaches the candidates starting on ASCII; a one-shot detect starts them at the first non-ASCII
    run: Final[bytes] = b" plain words between two russian sentences, long enough to skip over "
    detector: Final[EncodingDetector] = EncodingDetector()
    detector.feed(_DETECTOR_RUSSIAN)
    detector.feed(run + _DETECTOR_RUSSIAN)
    assert detector.close() == detect(_DETECTOR_RUSSIAN + run + _DETECTOR_RUSSIAN)


def test_disqualified_hebrew_candidate_matches_chunked_detection() -> None:
    detector = EncodingDetector()
    data = b"\xfa\xfaodbo\xdfd%{ m\xfa\xfa:\xfa"
    detector.feed(data[:13])
    detector.feed(data[13:])
    match = detect(data)
    assert match.encoding == "windows-1252"
    assert detector.close() == match


@pytest.mark.parametrize(
    ("chunk", "confidence"),
    [
        pytest.param(b"a", 1.0, id="ascii"),
        pytest.param(b"\x1b", 1.0, id="unfinished-escape"),
        pytest.param(b"\x81", 0.0, id="unfinished-lead-byte"),
    ],
)
def test_close_scores_the_end_of_stream(chunk: bytes, confidence: float) -> None:
    # close() feeds an empty final chunk, which the scanners hand to memchr and memcpy
    detector: Final[EncodingDetector] = EncodingDetector()
    detector.feed(chunk)
    assert detector.close() == EncodingMatch(
        encoding="windows-1252", confidence=confidence, language=None, codec="whatwg-windows-1252"
    )


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        pytest.param(
            b"\xdf",
            EncodingMatch(
                encoding="windows-1251", confidence=0.2236681958618107, language="Russian", codec="whatwg-windows-1251"
            ),
            id="disqualified-logical-hebrew",
        ),
        pytest.param(
            b"\xdf\xab!\xdf",
            EncodingMatch(
                encoding="windows-1251", confidence=0.22925234630391225, language="Russian", codec="whatwg-windows-1251"
            ),
            id="disqualified-logical-hebrew-past-punctuation-and-unmapped-bytes",
        ),
        pytest.param(
            b" ",
            EncodingMatch(
                encoding="windows-1255", confidence=0.2454175152749491, language="Hebrew", codec="whatwg-windows-1255"
            ),
            id="surviving-logical-hebrew",
        ),
    ],
)
def test_streamed_hebrew_keeps_its_punctuation_tiebreak(prefix: bytes, expected: EncodingMatch) -> None:
    detector: Final[EncodingDetector] = EncodingDetector()
    detector.feed(prefix)
    detector.feed((f"{'שלום! ' * 16}!שלום").encode("iso-8859-8"))
    assert detector.close() == expected


def test_a_leading_bom_finishes_the_stream_early() -> None:
    detector = EncodingDetector()
    detector.feed(b"\xef\xbb\xbf")
    assert detector.done
    detector.feed("Ω".encode("cp1253"))  # ignored: the mark already decided the stream
    assert detector.close() == EncodingMatch(
        encoding="UTF-8-SIG", confidence=1.0, language=None, bom=True, codec="whatwg-utf-8-sig"
    )


def test_a_bom_split_across_feeds_still_finishes_early() -> None:
    detector = EncodingDetector()
    detector.feed(b"\xef\xbb")
    assert not detector.done
    detector.feed(b"\xbftail")
    assert detector.done


def test_00_00_fe_ff_is_not_a_mark_so_the_stream_keeps_reading() -> None:
    # the UTF-32BE byte pattern is no WHATWG mark; the stream must content-sniff it, not finish early on a BOM
    detector: Final[EncodingDetector] = EncodingDetector()
    detector.feed(b"\x00\x00\xfe\xff")
    assert not detector.done
    assert not detector.close().bom


@pytest.mark.parametrize(
    ("chunk", "encoding"),
    [
        pytest.param(b"\xff\xfe", "UTF-16LE", id="utf-16le-bare-mark"),
        pytest.param(b"\xff\xfeh\x00", "UTF-16LE", id="utf-16le-non-zero-pair"),
        pytest.param(b"\xff\xfe\x00\x00", "UTF-16LE", id="ff-fe-00-00-is-utf-16le"),
        pytest.param(b"\xfe\xff\x00h", "UTF-16BE", id="utf-16be"),
    ],
)
def test_a_resolved_mark_finishes_early(chunk: bytes, encoding: str) -> None:
    # a mark that a single chunk resolves finishes the stream at once
    detector: Final[EncodingDetector] = EncodingDetector()
    detector.feed(chunk)
    assert detector.done
    assert detector.close() == EncodingMatch(
        encoding=encoding, confidence=1.0, language=None, bom=True, codec=f"whatwg-{encoding.casefold()}"
    )


def test_close_caches_its_result() -> None:
    detector = EncodingDetector()
    detector.feed(b"plain ascii")
    assert detector.close() is detector.close()
    assert detector.result == EncodingMatch(
        encoding="windows-1252", confidence=1.0, language=None, codec="whatwg-windows-1252"
    )


def test_close_without_a_feed_reports_no_encoding() -> None:
    assert EncodingDetector().close() == EncodingMatch(encoding=None, confidence=0.0, language=None)


def test_reset_starts_a_fresh_stream() -> None:
    detector = EncodingDetector()
    detector.feed(b"\xff\xfeh\x00")
    detector.close()
    detector.reset()
    assert detector.result is None
    assert not detector.done
    detector.feed(_DETECTOR_RUSSIAN)
    assert detector.close().encoding == "windows-1251"


def test_detector_honors_its_options() -> None:
    detector = EncodingDetector(Detection.chardet())
    detector.feed(b"\x81\n")
    assert detector.close() == EncodingMatch(encoding=None, confidence=0.0, language=None)


# The samples exercise every structural answer the stream can reach: an escape-driven
# ISO-2022-JP run, a multi-byte UTF-8 run, a CJK run whose sequences straddle the feeds, and a
# pure-ASCII run that carries no evidence at all.
_SAMPLES: Final[list[tuple[bytes, str | None] | pytest.ParameterSet]] = [
    pytest.param("日本語のテキスト".encode("iso-2022-jp"), id="iso-2022-jp"),
    pytest.param("café naïve 日本語".encode(), id="utf-8"),
    pytest.param(b"plain ascii only", id="ascii"),
    pytest.param("中文简体测试".encode("gbk"), id="gbk"),
    pytest.param("日本語のテキスト".encode("shift_jis"), id="shift_jis"),
    pytest.param("日本語のテキスト and a longer plain ASCII suffix".encode("shift_jis"), id="shift-jis-before-ascii"),
    pytest.param(b"\x81\n" + b"plain ASCII suffix " * 4, id="unmapped-byte-before-ascii"),
    pytest.param("한국어 텍스트".encode("euc-kr"), id="euc-kr"),
    pytest.param("中文字元測試".encode("big5"), id="big5"),
    pytest.param("Příliš žluťoučký kůň".encode("windows-1250"), id="windows-1250"),
    pytest.param("Съешь же ещё этих".encode("windows-1251"), id="windows-1251"),
]


@pytest.mark.parametrize("raw", _SAMPLES)
@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in (1, 2, 3, 5, 8)])
def test_chunk_boundaries_never_change_the_answer(raw: bytes, size: int) -> None:
    # a multi-byte sequence, an escape, and the two bytes the scoring looks back at all straddle
    # these boundaries; the detector carries each across the feed
    detector = EncodingDetector()
    for start in range(0, len(raw), size):
        detector.feed(raw[start : start + size])
    assert detector.close() == detect(raw)


# Settled answers (a byte-order mark, a <meta> declaration, pure ASCII, structural UTF-8) skip the candidates, and the
# detector shares one match per settled answer under the default options. Streams of each must answer what one-shot
# detect does under every option set, at a chunk size that splits the mark and one that feeds everything at once.
_SETTLED_STREAMS: Final[tuple[bytes, ...]] = (
    b"\xef\xbb\xbf<p>hi</p>",
    b"\xff\xfe<\x00p\x00>\x00",
    b'<meta charset="iso-8859-2"><p>x</p>',
    b"<p>plain ascii</p>",
    "<p>café 日本</p>".encode(),
)
_SETTLED_OPTIONS: Final[tuple[Detection, ...]] = (
    Detection(),
    Detection(threshold=0.9),
    Detection(excluded=frozenset({"UTF-8-SIG"})),
)


@pytest.mark.parametrize("raw", _SETTLED_STREAMS)
@pytest.mark.parametrize("options", _SETTLED_OPTIONS)
@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in (2, 4096)])
def test_settled_streams_answer_what_one_shot_does(raw: bytes, options: Detection, size: int) -> None:
    detector = EncodingDetector(options)
    for start in range(0, len(raw), size):
        detector.feed(raw[start : start + size])
    assert detector.close() == detect(raw, options)


def test_a_long_stream_still_answers_what_one_shot_does() -> None:
    # the candidates carry state, not bytes, so 4096 feeds cost what one does and answer the same
    chunk = "Съешь же ещё этих мягких".encode("windows-1251")
    detector = EncodingDetector()
    for _ in range(4096):
        detector.feed(chunk)
    assert detector.close() == detect(chunk * 4096)


@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in (16, 5000)])
def test_gathered_ascii_chunks_answer_what_one_shot_does(size: int) -> None:
    # ASCII chunks after the first non-ASCII byte wait in a buffer, flushed when it fills or a non-ASCII chunk comes
    raw: Final[bytes] = _DETECTOR_RUSSIAN + b" plain ascii words" * 300 + _DETECTOR_RUSSIAN
    detector: Final[EncodingDetector] = EncodingDetector()
    for start in range(0, len(raw), size):
        detector.feed(raw[start : start + size])
    assert detector.close() == detect(raw)


def test_reset_before_close_starts_a_fresh_stream() -> None:
    detector: Final[EncodingDetector] = EncodingDetector()
    detector.feed(b"\xef\xbb\xbf")
    detector.reset()
    detector.feed(_DETECTOR_RUSSIAN)
    assert detector.close() == detect(_DETECTOR_RUSSIAN)


def test_the_detector_takes_its_chunk_by_keyword() -> None:
    detector: Final[EncodingDetector] = EncodingDetector()
    detector.feed(data=_DETECTOR_RUSSIAN)
    assert detector.close() == detect(_DETECTOR_RUSSIAN)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"\x1b", id="escape-alone"),
        pytest.param(b"\x1b$", id="escape-truncated"),
        pytest.param(b"text\x1b(", id="escape-truncated-after-text"),
    ],
)
def test_a_stream_ending_mid_escape_is_not_iso_2022_jp(raw: bytes) -> None:
    # the escape never completes, so the structural ISO-2022-JP answer is off the table
    detector = EncodingDetector()
    for byte in raw:
        detector.feed(bytes([byte]))
    assert detector.close() == detect(raw)
    assert detect(raw).encoding != "ISO-2022-JP"


def test_repeated_iso_2022_jp_escapes_match_bytewise_detection() -> None:
    detector = EncodingDetector()
    raw = b"\x1b(J\x1b(J"
    for byte in raw:
        detector.feed(bytes((byte,)))
    assert detector.close() == detect(raw)


@pytest.mark.parametrize("shift", [pytest.param(0x0E, id="shift-out"), pytest.param(0x0F, id="shift-in")])
def test_a_shift_code_before_the_escape_rules_out_iso_2022_jp(shift: int) -> None:
    # the decoder's ASCII state rejects both shift codes, so the escape that follows cannot
    # rescue the stream, and the scan stays dead through every later feed
    raw = bytes([ord("a"), shift, ord("b")]) + "日本語".encode("iso-2022-jp")
    assert detect(raw).encoding != "ISO-2022-JP"
    detector = EncodingDetector()
    for byte in raw:
        detector.feed(bytes([byte]))
    assert detector.close() == detect(raw)


_EMPTY_ENCODING_MATCH: Final[EncodingMatch] = EncodingMatch(encoding=None, confidence=0.0, language=None)
_OPTIONS_RUSSIAN: Final[bytes] = "Привет мир, как дела".encode("cp1251")


@pytest.mark.parametrize(
    "threshold",
    [
        pytest.param(-0.1, id="below-zero"),
        pytest.param(1.1, id="above-one"),
    ],
)
def test_out_of_range_threshold_is_rejected(threshold: float) -> None:
    with pytest.raises(ValueError, match=r"threshold must be within \[0\.0, 1\.0\]"):
        Detection(threshold=threshold)


def test_allowed_and_excluded_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        Detection(allowed=frozenset({"utf-8"}), excluded=frozenset({"gbk"}))


def test_chardet_preset_mirrors_the_minimum_threshold() -> None:
    assert Detection.chardet() == Detection(threshold=0.2)


def test_chardet_preset_drops_the_no_evidence_fallback() -> None:
    assert detect(b"\x81\n", Detection.chardet()) == _EMPTY_ENCODING_MATCH


def test_chardet_preset_keeps_a_confident_result() -> None:
    assert detect(_OPTIONS_RUSSIAN, Detection.chardet()).encoding == "windows-1251"


def test_threshold_filters_detect_all() -> None:
    kept = detect_all(_OPTIONS_RUSSIAN, Detection(threshold=0.5))
    assert kept == [match for match in detect_all(_OPTIONS_RUSSIAN) if match.confidence >= 0.5]
    assert len(kept) == 1


def test_allowed_restricts_the_winner() -> None:
    assert detect(_OPTIONS_RUSSIAN, Detection(allowed=frozenset({"KOI8-U", "IBM866"}))).encoding == "KOI8-U"


def test_allowed_matches_names_case_insensitively() -> None:
    assert detect(_OPTIONS_RUSSIAN, Detection(allowed=frozenset({"koi8-u"}))).encoding == "KOI8-U"


def test_allowed_ruling_every_candidate_out_yields_no_match() -> None:
    assert detect(_OPTIONS_RUSSIAN, Detection(allowed=frozenset({"UTF-8"}))) == _EMPTY_ENCODING_MATCH


def test_excluded_promotes_the_runner_up() -> None:
    runner_up = detect_all(_OPTIONS_RUSSIAN)[1]
    assert detect(_OPTIONS_RUSSIAN, Detection(excluded=frozenset({"windows-1251"}))) == runner_up


def test_excluding_a_certain_result_yields_no_match() -> None:
    assert detect(b"\xef\xbb\xbfx", Detection(excluded=frozenset({"utf-8-sig"}))) == _EMPTY_ENCODING_MATCH


def test_language_hint_prefers_the_matching_model() -> None:
    match = detect(_OPTIONS_RUSSIAN, Detection(language="Hebrew"))
    assert (match.encoding, match.language) == ("windows-1255", "Hebrew")


def test_language_hint_without_positive_evidence_changes_nothing() -> None:
    assert detect(_OPTIONS_RUSSIAN, Detection(language="Thai")) == detect(_OPTIONS_RUSSIAN)


# Czech in ISO-8859-2. The hint decides on text like this, where the Central European encodings score alike and the
# Cyrillic and Western TLDs disagree about whether ISO-8859-2 or windows-1252 should read it.
_CZECH: Final[bytes] = "Příliš žluťoučký kůň úpěl ďábelské ódy".encode("iso-8859-2")

# The same text, short enough that no candidate scores well and the TLD's own encoding is left to answer.
_CZECH_SHORT: Final[bytes] = "Příliš žluťoučký kůň úpěl".encode("iso-8859-2")

# The two Chinese scripts, which nothing but a TLD separates at this length.
_TRADITIONAL: Final[bytes] = "繁體中文字元測試內容".encode("big5")
_SIMPLIFIED: Final[bytes] = "天地玄黄宇宙洪荒日月盈昃".encode("gb18030")

# Byte soup that kills one candidate of a sibling pair and leaves the other scoring, so the TLD falls back on its
# sibling script. Big5 dies on the first, ISO-8859-2 on the second; chardetng answers as asserted below.
_NO_TRADITIONAL: Final[bytes] = bytes.fromhex("e098bcf194a9")
_NO_CENTRAL_ISO: Final[bytes] = bytes.fromhex("a699dd")

# Byte soup too short to hold a two-letter word in either script. ISO-8859-6 is native to .sa and windows-1256 to
# .my without being what either domain expects, so each one scores there and nowhere else.
_SHORT_ARABIC_ISO: Final[bytes] = bytes.fromhex("bfed")
_SHORT_ARABIC_WINDOWS: Final[bytes] = bytes.fromhex("d6a59ebd")


@pytest.mark.parametrize(
    ("tld", "encoding"),
    [
        pytest.param(None, "ISO-8859-2", id="no-hint"),
        pytest.param("com", "ISO-8859-2", id="a-generic-label-hints-nothing"),
        pytest.param("cz", "ISO-8859-2", id="the-native-encoding-keeps-its-score"),
        pytest.param("pl", "ISO-8859-2", id="a-sibling-central-label-agrees"),
        pytest.param("ru", "windows-1252", id="cyrillic-zeroes-the-central-candidates"),
        pytest.param("de", "windows-1252", id="a-western-label-does-too"),
        pytest.param("zz", "windows-1252", id="an-unlisted-country-code-reads-as-western"),
        pytest.param("edu", "windows-1252", id="edu-reads-as-western"),
        pytest.param("xn--unlisted", "ISO-8859-2", id="an-unlisted-punycode-label-hints-nothing"),
        pytest.param("longlabel", "ISO-8859-2", id="a-label-that-is-not-punycode-hints-nothing"),
        pytest.param("xn--p1a", "ISO-8859-2", id="a-label-too-short-to-be-punycode-hints-nothing"),
        pytest.param("th", "ISO-8859-2", id="a-script-absent-from-the-bytes-penalizes-nothing"),
    ],
)
def test_the_tld_narrows_the_candidates(tld: str | None, encoding: str) -> None:
    assert detect(_CZECH, Detection(tld=tld)).encoding == encoding


def test_the_tlds_own_encoding_answers_when_nothing_outscores_it() -> None:
    # windows-1251 finds no Cyrillic word here, so it never scores. It wins because a Cyrillic TLD zeroes the
    # Central candidates, and this text is too short for the Western one to stay ahead of a default.
    assert detect(_CZECH_SHORT, Detection(tld="ru")).encoding == "windows-1251"


def test_a_punycode_label_classifies_like_the_ascii_one() -> None:
    assert detect(_CZECH_SHORT, Detection(tld="xn--p1ai")) == detect(_CZECH_SHORT, Detection(tld="ru"))


def test_a_traditional_tld_picks_traditional_over_simplified() -> None:
    assert detect(_SIMPLIFIED, Detection(tld="tw")).encoding == "Big5"


def test_a_simplified_tld_picks_simplified_over_traditional() -> None:
    assert detect(_TRADITIONAL, Detection(tld="cn")).encoding == "GBK"


def test_a_traditional_tld_falls_back_on_simplified_when_no_big5_survives() -> None:
    # .tw expects Big5, which these bytes kill. chardetng then scores the page as though it came from a Simplified
    # domain, handing GBK the point the TLD's own encoding would have taken and penalizing the Latin candidate that
    # wins with no TLD at all.
    assert detect(_NO_TRADITIONAL).encoding == "windows-1252"
    assert detect(_NO_TRADITIONAL, Detection(tld="tw")).encoding == "GBK"
    assert detect(_NO_TRADITIONAL, Detection(tld="tw")) == detect(_NO_TRADITIONAL, Detection(tld="cn"))


def test_a_tld_whose_sibling_script_is_also_absent_leaves_the_bytes_alone() -> None:
    # .tw expects Big5 and would settle for GBK, and Czech text carries neither. With both gone chardetng has no
    # expectation left to flip to, so it drops the TLD rather than let a dead sibling hand GBK the point.
    assert detect(_CZECH, Detection(tld="tw")).encoding == detect(_CZECH).encoding == "ISO-8859-2"


def test_a_central_iso_tld_falls_back_on_central_windows() -> None:
    # .hu expects ISO-8859-2, which these bytes kill, so windows-1250 inherits the expectation
    assert detect(_NO_CENTRAL_ISO, Detection(tld="hu")).encoding == "windows-1250"


def test_a_caseless_candidate_survives_the_word_gate_on_its_native_tld() -> None:
    # ISO-8859-6 never sees the two-letter Arabic word the gate asks for, so it scores only where its script is
    # native. .sa expects windows-1256, so nothing injects ISO-8859-6 as that domain's default.
    assert "ISO-8859-6" in _ranked(_SHORT_ARABIC_ISO, "sa")
    assert "ISO-8859-6" not in _ranked(_SHORT_ARABIC_ISO)


def _ranked(raw: bytes, tld: str | None = None) -> set[str | None]:
    # The encodings that scored, which is not every encoding ``detect_all`` reports: the winner leads it either way.
    return {match.encoding for match in detect_all(raw, Detection(tld=tld))[1:]}


def test_an_arabic_french_candidate_survives_the_word_gate_on_its_native_tld() -> None:
    # .my expects windows-1252 and counts windows-1256 as native, which is what carries it past the gate
    assert "windows-1256" in _ranked(_SHORT_ARABIC_WINDOWS, "my")
    assert "windows-1256" not in _ranked(_SHORT_ARABIC_WINDOWS)


def test_a_tld_whose_script_never_appears_stops_penalizing_the_rest() -> None:
    # .th expects windows-874, and no Thai appears in Czech text. With its expectation broken, chardetng reads the
    # label as mistaken rather than as evidence, so the bytes alone decide.
    assert detect(_CZECH, Detection(tld="th")).encoding == detect(_CZECH).encoding


def test_a_tld_whose_script_did_appear_keeps_penalizing() -> None:
    # Thai does score on Big5 bytes, so the expectation holds and Big5 pays the penalty .th levies on it
    assert detect(_TRADITIONAL, Detection(tld="th")).encoding == "windows-874"


def test_the_hint_cannot_overrule_a_structural_answer() -> None:
    # UTF-8 validity is a proof rather than a guess, and no label outvotes it
    assert detect("日本語のテキストです".encode(), Detection(tld="ru")).encoding == "UTF-8"


def test_the_hint_cannot_overrule_a_byte_order_mark() -> None:
    assert detect(b"\xff\xfe\x41\x00", Detection(tld="jp")).encoding == "UTF-16LE"


def test_the_hint_reorders_every_ranked_candidate() -> None:
    # detect_all reports the scores the winner came from, so the hint has to move the whole ranking. A runner-up
    # ranked by unadjusted scores would contradict the answer above it.
    assert detect_all(_CZECH, Detection(tld="ru"))[0].encoding == "windows-1252"


def test_the_streaming_detector_honors_the_hint() -> None:
    detector = EncodingDetector(Detection(tld="ru"))
    for start in range(0, len(_CZECH), 5):
        detector.feed(_CZECH[start : start + 5])
    assert detector.close() == detect(_CZECH, Detection(tld="ru"))


def test_reset_keeps_the_hint() -> None:
    detector = EncodingDetector(Detection(tld="ru"))
    detector.feed(_CZECH)
    detector.close()
    detector.reset()
    detector.feed(_CZECH)
    assert detector.close().encoding == "windows-1252"


@pytest.mark.parametrize(
    "tld",
    [
        pytest.param("", id="empty"),
        pytest.param("JP", id="upper-case"),
        pytest.param("example.jp", id="whole-hostname"),
        pytest.param(".jp", id="leading-dot"),
        pytest.param("рф", id="non-ascii"),
        pytest.param("jp ", id="trailing-space"),
    ],
)
def test_a_malformed_tld_is_rejected(tld: str) -> None:
    with pytest.raises(ValueError, match="rightmost DNS label"):
        Detection(tld=tld)


@pytest.mark.parametrize(
    "repeats",
    [pytest.param(1, id="sentence"), pytest.param(100, id="page"), pytest.param(10_000, id="long-prose")],
)
def test_language_detection_of_repeated_prose(repeats: int) -> None:
    assert detect_language(
        "There is no reason not to learn a new language every single year of your life. " * repeats
    ) == LanguageMatch(language="eng", confidence=1.0, script="Latin", name="English")


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"plain ASCII", id="ascii"),
        pytest.param(b"\xef\xbb\xbfhello", id="bom"),
        pytest.param("déjà vu, bientôt à Paris".encode("cp1252"), id="ambiguous"),
    ],
)
@pytest.mark.parametrize(
    "options",
    [
        pytest.param(Detection(), id="default"),
        pytest.param(Detection(threshold=0.9), id="threshold"),
        pytest.param(Detection(allowed=frozenset({"windows-1252"})), id="allowed"),
    ],
)
def test_detect_first_record_matches_ranked_and_streamed(data: bytes, options: Detection) -> None:
    expected: Final[EncodingMatch] = detect_all(data, options)[0]
    detector: Final[EncodingDetector] = EncodingDetector(options)
    detector.feed(data[:3])
    detector.feed(data[3:])
    assert detect(data, options) == detector.close() == expected


_ENCODING_RESULTS: Final[tuple[tuple[int, str, str], ...]] = (
    (0, "déjà vu, bientôt à Paris", "legacy"),
    (1, "A short plain message", "ascii"),
    (2, "hello", "bom"),
)
_MISDECODED_XFAIL: Final[pytest.MarkDecorator] = pytest.mark.xfail(
    strict=True, reason="Detector misdecodes the CP1252 fixture instead of preserving its accented text"
)
# UnicodeDammit guesses with the first detector it finds, cchardet before chardet and charset_normalizer, so it
# misdecodes the legacy fixture exactly when it hands it to one of those two
_BS4_DETECTOR: Final[str | None] = (
    getattr(importlib.import_module("bs4.dammit").chardet_module, "__name__", None) if find_spec("bs4") else None
)


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("case", "expected"),
    [pytest.param(case, expected, id=f"turbohtml-detect-{label}") for case, expected, label in _ENCODING_RESULTS],
)
def test_encoding_result_turbohtml_detect_preserves_decoded_text(case: int, expected: str) -> None:
    library: Final[ModuleType] = pytest.importorskip("turbohtml.detect")
    data: Final[bytes] = cast("tuple[int, bytes]", INPUTS["encoding-result"]()[case][1])[1]
    assert data.decode(library.detect(data).codec).removeprefix("﻿") == expected


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("case", "expected"),
    [pytest.param(case, expected, id=f"turbohtml-stream-{label}") for case, expected, label in _ENCODING_RESULTS],
)
def test_encoding_result_turbohtml_stream_preserves_decoded_text(case: int, expected: str) -> None:
    library: Final[ModuleType] = pytest.importorskip("turbohtml.detect")
    data: Final[bytes] = cast("tuple[int, bytes]", INPUTS["encoding-result"]()[case][1])[1]
    detector: Final[EncodingDetector] = library.EncodingDetector()
    detector.feed(data)
    codec = detector.close().codec
    assert codec is not None
    assert data.decode(codec).removeprefix("﻿") == expected


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("module", "case", "expected"),
    [
        pytest.param(
            module,
            case,
            expected,
            id=f"{module}-detect-{label}",
            marks=_MISDECODED_XFAIL if module == "chardet" and case == 0 else (),
        )
        for module in ("chardet", "cchardet")
        for case, expected, label in _ENCODING_RESULTS
    ],
)
def test_encoding_result_chardet_detect_preserves_decoded_text(module: str, case: int, expected: str) -> None:
    library: Final[ModuleType] = pytest.importorskip(module)
    data: Final[bytes] = cast("tuple[int, bytes]", INPUTS["encoding-result"]()[case][1])[1]
    assert data.decode(library.detect(data)["encoding"]).removeprefix("﻿") == expected


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("module", "case", "expected"),
    [
        pytest.param(
            module,
            case,
            expected,
            id=f"{module}-stream-{label}",
            marks=_MISDECODED_XFAIL if module == "chardet" and case == 0 else (),
        )
        for module in ("chardet", "cchardet")
        for case, expected, label in _ENCODING_RESULTS
    ],
)
def test_encoding_result_chardet_stream_preserves_decoded_text(module: str, case: int, expected: str) -> None:
    library: Final[ModuleType] = pytest.importorskip(module)
    data: Final[bytes] = cast("tuple[int, bytes]", INPUTS["encoding-result"]()[case][1])[1]
    detector = library.UniversalDetector()
    detector.feed(data)
    detector.close()
    assert data.decode(detector.result["encoding"]).removeprefix("﻿") == expected


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(case, expected, id=f"charset_normalizer-{label}", marks=_MISDECODED_XFAIL if case == 0 else ())
        for case, expected, label in _ENCODING_RESULTS
    ],
)
def test_encoding_result_charset_normalizer_preserves_decoded_text(case: int, expected: str) -> None:
    library: Final[ModuleType] = pytest.importorskip("charset_normalizer")
    data: Final[bytes] = cast("tuple[int, bytes]", INPUTS["encoding-result"]()[case][1])[1]
    match = library.from_bytes(data).best()
    assert match is not None
    assert data.decode(match.encoding).removeprefix("﻿") == expected


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(
            case,
            expected,
            id=f"bs4-{label}",
            marks=_MISDECODED_XFAIL if case == 0 and _BS4_DETECTOR in {"chardet", "charset_normalizer"} else (),
        )
        for case, expected, label in _ENCODING_RESULTS
    ],
)
def test_encoding_result_bs4_preserves_decoded_text(case: int, expected: str) -> None:
    library: Final[ModuleType] = pytest.importorskip("bs4")
    data: Final[bytes] = cast("tuple[int, bytes]", INPUTS["encoding-result"]()[case][1])[1]
    encoding: Final[str | None] = library.UnicodeDammit(data).original_encoding
    assert encoding is not None
    assert data.decode(encoding).removeprefix("﻿") == expected


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("case", "expected"),
    [pytest.param(case, expected, id=f"resiliparse-{label}") for case, expected, label in _ENCODING_RESULTS],
)
def test_encoding_result_resiliparse_preserves_decoded_text(case: int, expected: str) -> None:
    library: Final[ModuleType] = pytest.importorskip("resiliparse.parse.encoding")
    data: Final[bytes] = cast("tuple[int, bytes]", INPUTS["encoding-result"]()[case][1])[1]
    encoding: Final[str] = library.detect_encoding(data)
    assert encoding is not None
    assert data.decode(encoding).removeprefix("﻿") == expected


# chardetng drops an unmapped Big5 pair's ASCII trail: it neither scores the letter nor reads it again. Rewinding onto
# the trail charged the letter the Latin-adjacency penalty, which can decide the answer.
_BIG5_TEXT: Final[bytes] = "繁體中文測試這是一個很長的句".encode("big5")
_BIG5_UNMAPPED_PAIR: Final[tuple[bytes, ...]] = tuple(
    b"\xc0\xe0" * 4 + _BIG5_TEXT + bytes([lead, trail]) + _BIG5_TEXT for lead in range(0x81, 0x87) for trail in b"Aa"
)
_BIG5_CHUNK_SIZES: Final[tuple[int, ...]] = (1, 2, 3, 16)


@pytest.mark.parametrize(
    "raw", [pytest.param(raw, id=f"pair-{index}") for index, raw in enumerate(_BIG5_UNMAPPED_PAIR)]
)
def test_unmapped_big5_pair_with_ascii_trail_keeps_big5(raw: bytes) -> None:
    assert detect(raw).encoding == "Big5"


@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in _BIG5_CHUNK_SIZES])
@pytest.mark.parametrize(
    "raw", [pytest.param(raw, id=f"pair-{index}") for index, raw in enumerate(_BIG5_UNMAPPED_PAIR)]
)
def test_unmapped_big5_pair_with_ascii_trail_keeps_big5_streamed(raw: bytes, size: int) -> None:
    detector = EncodingDetector()
    for start in range(0, len(raw), size):
        detector.feed(raw[start : start + size])
    assert detector.close().encoding == "Big5"


# a letter right before the pair takes chardetng's Latin-adjacency penalty on top of the pair's own
_BIG5_LETTER_BEFORE_PAIR: Final[tuple[bytes, ...]] = tuple(
    b"x" + bytes([lead, trail]) + _BIG5_TEXT for lead in range(0x81, 0x87) for trail in b"Aa"
)


@pytest.mark.parametrize(
    "raw", [pytest.param(raw, id=f"letter-{index}") for index, raw in enumerate(_BIG5_LETTER_BEFORE_PAIR)]
)
def test_letter_before_unmapped_big5_pair_is_windows_1252(raw: bytes) -> None:
    assert detect(raw).encoding == "windows-1252"


@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in _BIG5_CHUNK_SIZES])
@pytest.mark.parametrize(
    "raw", [pytest.param(raw, id=f"letter-{index}") for index, raw in enumerate(_BIG5_LETTER_BEFORE_PAIR)]
)
def test_letter_before_unmapped_big5_pair_is_windows_1252_streamed(raw: bytes, size: int) -> None:
    detector = EncodingDetector()
    for start in range(0, len(raw), size):
        detector.feed(raw[start : start + size])
    assert detector.close().encoding == "windows-1252"


# chardetng's GBK candidate survives a lead, a digit pair and a 0xFF. The decoder pushes the digit pair back with the
# 0xFF, chardetng takes the single-byte-extension penalty and resets its decoder, so the pushed-back bytes are never
# read again. Reading them again killed the candidate, and under a .cn label IBM866 won instead.
_CHARDETNG_GBK: Final[tuple[bytes, ...]] = (b"\xe26\x90\xff0\xa3\x89", b"yr69\xe26\x90\xff0\xa3\x89")
# a lead followed directly by 0xFF is a two-byte error chardetng scores as death, so the candidate stays out
_LEAD_BEFORE_FF: Final[bytes] = bytes.fromhex("90ffd836a0328037")
_GBK_CHUNK_SIZES: Final[tuple[int, ...]] = (1, 2, 3, 16)
_CN: Final[Detection] = Detection(tld="cn")


@pytest.mark.parametrize("raw", [pytest.param(raw, id=f"gbk-{index}") for index, raw in enumerate(_CHARDETNG_GBK)])
def test_pushed_back_digits_after_a_lead_keep_gbk(raw: bytes) -> None:
    assert detect(raw, _CN).encoding == "GBK"


@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in _GBK_CHUNK_SIZES])
@pytest.mark.parametrize("raw", [pytest.param(raw, id=f"gbk-{index}") for index, raw in enumerate(_CHARDETNG_GBK)])
def test_pushed_back_digits_after_a_lead_keep_gbk_streamed(raw: bytes, size: int) -> None:
    detector = EncodingDetector(_CN)
    for start in range(0, len(raw), size):
        detector.feed(raw[start : start + size])
    assert detector.close().encoding == "GBK"


@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in _GBK_CHUNK_SIZES])
def test_lead_before_ff_is_windows_1256_streamed(size: int) -> None:
    detector = EncodingDetector(_CN)
    for start in range(0, len(_LEAD_BEFORE_FF), size):
        detector.feed(_LEAD_BEFORE_FF[start : start + size])
    assert detector.close().encoding == "windows-1256"


def test_lead_before_ff_is_windows_1256() -> None:
    assert detect(_LEAD_BEFORE_FF, _CN).encoding == "windows-1256"


_GBK_U: Final[bytes] = "中华人民共和国的首都是北京市这里有很多历史文化遗产".encode("gbk")
_GBK_T: Final[bytes] = "中华人民共和国的首都是北京市这里有".encode("gbk")
# a four-byte prefix whose fourth byte is 0xFF, or ASCII after a 0xA0 or 0xFE lead, is pushed back by the decoder,
# and chardetng's first single-byte-extension rule resets its decoder there as the 0xFF rule does
_PUSHED_BACK_FOURTH_BYTE: Final[tuple[bytes, ...]] = (
    _GBK_U * 2 + b"\x81\x36\xfe\xff" + _GBK_U,
    b"\xc0\xe0" * 4 + _GBK_T + b"\x81\x36\xa0\x20" + _GBK_T,
)


@pytest.mark.parametrize(
    "raw", [pytest.param(raw, id=f"fourth-{index}") for index, raw in enumerate(_PUSHED_BACK_FOURTH_BYTE)]
)
def test_pushed_back_fourth_byte_keeps_gbk(raw: bytes) -> None:
    assert detect(raw).encoding == "GBK"


@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in _GBK_CHUNK_SIZES])
@pytest.mark.parametrize(
    "raw", [pytest.param(raw, id=f"fourth-{index}") for index, raw in enumerate(_PUSHED_BACK_FOURTH_BYTE)]
)
def test_pushed_back_fourth_byte_keeps_gbk_streamed(raw: bytes, size: int) -> None:
    detector = EncodingDetector()
    for start in range(0, len(raw), size):
        detector.feed(raw[start : start + size])
    assert detector.close().encoding == "GBK"


# Each input is an ASCII run followed by a non-ASCII byte, a shape chardetng's feed starts two bytes before. Upstream
# scored the run too and picked another single-byte encoding; chardetng answers windows-1252 for all of them, as the
# tests/conformance detector oracle confirms.
_CHARDETNG_WINDOWS_1252: Final[tuple[bytes, ...]] = (
    b"a1111\xaa ",
    b"N.x\xaa.\xa9",
    b"zz11\xba \xa9",
    b"N.x\xba  \xaa",
    b"a11\xaa \xc9\xba",
    b"n.1\xaa \xe9\xa9",
    b"a11\xba \xc9\xaa.",
)
_CHUNK_SIZES: Final[tuple[int, ...]] = (1, 2, 3, 5, 7, 16)


@pytest.mark.parametrize(
    "raw", [pytest.param(raw, id=f"case-{index}") for index, raw in enumerate(_CHARDETNG_WINDOWS_1252)]
)
def test_ascii_prefix_one_shot_matches_chardetng(raw: bytes) -> None:
    assert detect(raw).encoding == "windows-1252"


@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in _CHUNK_SIZES])
@pytest.mark.parametrize(
    "raw", [pytest.param(raw, id=f"case-{index}") for index, raw in enumerate(_CHARDETNG_WINDOWS_1252)]
)
def test_ascii_prefix_streamed_matches_chardetng(raw: bytes, size: int) -> None:
    detector = EncodingDetector()
    for start in range(0, len(raw), size):
        detector.feed(raw[start : start + size])
    assert detector.close().encoding == "windows-1252"


# ASCII runs of every length between multi-byte and single-byte text, so each candidate kind skips runs in the
# middle of a sequence it is scoring. The digests pin the answers upstream/main gives for these bytes, one-shot and
# streamed at several chunk sizes; a skip that drops or misreads a run changes a digest.
_RUN_SEED: Final[int] = 20261118
_RUN_CASES: Final[int] = 300
_RUN_TEXTS: Final[tuple[tuple[str, str], ...]] = (
    ("日本語のテキストをここに書きます。今日は", "shift_jis"),
    ("日本語のテキストｱｲｳﾞﾟ。今日は", "shift_jis"),
    ("日本語のテキストをここに書きます。今日は", "euc_jp"),
    ("中文测试文本，你好世界（重要）", "gbk"),  # ruff:ignore[ambiguous-unicode-character-string]  # non-Latin sample text is the input under test
    ("中文测试文本，你好世界。鿿", "gb18030"),  # ruff:ignore[ambiguous-unicode-character-string]  # non-Latin sample text is the input under test
    ("繁體中文測試，你好世界。今天天氣很好", "big5"),  # ruff:ignore[ambiguous-unicode-character-string]  # non-Latin sample text is the input under test
    ("한국어 텍스트입니다. 안녕하세요 세계", "euc_kr"),
    ("漢字漢字漢字漢字", "euc_kr"),
    ("Привет мир, как дела? Программирование Ёж", "cp1251"),
    ("Привет мир, как дела? Программирование", "koi8_r"),
    ("Γειά σου κόσμε, τι κάνεις; Καλημέρα", "cp1253"),  # ruff:ignore[ambiguous-unicode-character-string]  # non-Latin sample text is the input under test
    ("שלום עולם, מה שלומך? בסדר גמור", "cp1255"),
    ("مرحبا بالعالم، كيف حالك؟ الحمد لله", "cp1256"),
    ("Merhaba dünya, nasılsın? Çok güzel değil mi", "cp1254"),  # ruff:ignore[ambiguous-unicode-character-string]  # non-Latin sample text is the input under test
    ("Příliš žluťoučký kůň, dobrý den. Ahoj světe", "cp1250"),
    ("Précédemment, la créativité française déjà", "cp1252"),
    ("สวัสดีครับ ยินดีต้อนรับ วันนี้", "cp874"),
    ("Sveiki pasaulis, kā tev klājas? Ačiū labai", "cp1257"),
)
_RUN_WORDS: Final[tuple[bytes, ...]] = (
    b"the",
    b"ABC",
    b" ",
    b"  ",
    b"x",
    b"1234",
    b"I V X",
    b"Ma",
    b"N.",
    b"n.",
    b"No",
    b"<p>",
    b"&amp;",
    b"ok.",
    b"Hi!",
    b"a",
    b"Z9",
)
_RUN_RANKING_DIGEST: Final[str] = "b235bb978fb8ad8dcd87b0316e9d2adfec02fd69085c2a97052afa8cd0048a70"
_RUN_STREAM_DIGEST: Final[str] = "0fd1e8738c485d9ace1b264f7e600288caf456baf474a6b080b04c3155960cd8"


def test_ascii_runs_keep_the_ranking() -> None:
    ranked = [[(m.encoding, m.confidence, m.language, m.bom) for m in detect_all(raw)] for raw in _run_corpus()]
    assert _digest(repr(ranked)) == _RUN_RANKING_DIGEST


def _run_corpus() -> list[bytes]:
    rng = random.Random(_RUN_SEED)  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # a corpus seed, not a security decision
    return [
        b"".join(_run_ascii(rng) if rng.random() < 0.55 else _run_text(rng) for _ in range(rng.randrange(1, 7)))
        for _ in range(_RUN_CASES)
    ]


def _run_ascii(rng: random.Random) -> bytes:
    if rng.random() < 0.5:
        return b"".join(rng.choice(_RUN_WORDS) for _ in range(rng.randrange(1, 6)))
    return bytes(rng.randrange(0x20, 0x7F) for _ in range(rng.randrange(1, 24)))


def _run_text(rng: random.Random) -> bytes:
    text, codec = rng.choice(_RUN_TEXTS)
    return text[rng.randrange(len(text)) :].encode(codec)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("size", [pytest.param(size, id=f"chunk-{size}") for size in (1, 3, 16, 4096)])
def test_ascii_runs_keep_the_streamed_answer(size: int) -> None:
    answers: list[tuple[str | None, float, str | None, bool]] = []
    for raw in _run_corpus():
        detector = EncodingDetector()
        for start in range(0, len(raw), size):
            detector.feed(raw[start : start + size])
        match = detector.close()
        answers.append((match.encoding, match.confidence, match.language, match.bom))
    assert _digest(repr(answers)) == _RUN_STREAM_DIGEST


# One byte per feed rarely reaches the in-feed ASCII skip (only held-over gb18030 bytes can form a run), so
# byte-at-a-time streaming is the reference each run is checked against. Each input lands an ASCII run right after
# a candidate's pending state: an unfinished lead, a held gb18030 pair, a pending Big5 combining mark, a pending GBK
# scalar, or a word the space closes.
_RUN_EDGES: Final[tuple[bytes, ...]] = (
    b"\x81a hello world",
    b"\x81\x30abc def",
    "中文 hello world 测试".encode("gbk"),
    b"\xb0\x40abc def",
    "繁體 hello world 字".encode("big5"),
    b"\x88\x62abc def",
    "漢字 ab cd 漢字".encode("euc_kr"),
    "日本語 hello world 日本".encode("shift_jis"),
    "日本語 hello world 日本".encode("euc_jp"),
    "Привет abcdefghijk мир".encode("cp1251"),
    "مرحبا hello world مرحبا".encode("cp1256"),
    "שלום hello world שלום".encode("cp1255"),
    "Příliš abcdefgh žluťoučký".encode("cp1250"),
    b"1\xaa abc def ",
    b"caf\xe9 abcdefghijklmnop",
)


@pytest.mark.parametrize("raw", [pytest.param(raw, id=f"edge-{index}") for index, raw in enumerate(_RUN_EDGES)])
def test_ascii_runs_match_byte_at_a_time_detection(raw: bytes) -> None:
    detector = EncodingDetector()
    for index in range(len(raw)):
        detector.feed(raw[index : index + 1])
    assert detect(raw) == detector.close()
