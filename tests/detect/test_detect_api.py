"""detect()/detect_all() (issue #315): the standalone surface over the same C sniff parse() runs on bytes."""

from __future__ import annotations

from itertools import islice, product

import pytest

from turbohtml import parse
from turbohtml.detect import (
    EncodingMatch,
    LanguageDetection,
    LanguageMatch,
    detect,
    detect_all,
    detect_language,
)

_RUSSIAN_1251 = "Программирование помогает понять структуру вычислительных систем сегодня здесь.".encode("cp1251")


def test_empty_input_has_no_encoding() -> None:
    assert detect(b"") == EncodingMatch(None, 0.0, None)


def test_pure_ascii_is_certain() -> None:
    assert detect(b"<p>hello world</p>") == EncodingMatch("ascii", 1.0, None)


def test_meta_prescan_is_certain_without_a_bom() -> None:
    assert detect(b"<meta charset=iso-8859-2><p>x</p>") == EncodingMatch("ISO-8859-2", 1.0, None, bom=False)


@pytest.mark.parametrize(
    ("raw", "encoding"),
    [
        pytest.param(b"\xef\xbb\xbfhello", "UTF-8-SIG", id="utf-8-sig"),
        pytest.param(b"\xff\xfeh\x00", "UTF-16LE", id="utf-16le"),
        pytest.param(b"\xfe\xff\x00h", "UTF-16BE", id="utf-16be"),
        pytest.param(b"\xff\xfe\x00\x00h\x00\x00\x00", "UTF-32LE", id="utf-32le"),
        pytest.param(b"\x00\x00\xfe\xff\x00\x00\x00h", "UTF-32BE", id="utf-32be"),
        pytest.param(b"\xff\xfe", "UTF-16LE", id="utf-16le-bare-mark"),
    ],
)
def test_byte_order_mark_reports_its_label_and_flag(raw: bytes, encoding: str) -> None:
    # a mark identifies the encoding unambiguously: UTF-8 reports UTF-8-SIG so a caller can strip it,
    # and the UTF-16/UTF-32 marks report their exact label; every marked result carries bom=True
    assert detect(raw) == EncodingMatch(encoding, 1.0, None, bom=True)


def test_bom_precedence_utf_32le_beats_the_utf_16le_prefix() -> None:
    # FF FE 00 00 is the UTF-32LE mark even though it starts with the UTF-16LE mark FF FE; the four-byte
    # signature is tested first, and a bare FF FE (no trailing 00 00) stays UTF-16LE
    assert detect(b"\xff\xfe\x00\x00").encoding == "UTF-32LE"
    assert detect(b"\xff\xfe\x01\x00").encoding == "UTF-16LE"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"\xef\xbb\xbfhello", id="utf-8-bom"),
        pytest.param(b"\xff\xfe\x00\x00h\x00\x00\x00", id="utf-32le-bom"),
    ],
)
def test_bom_labels_do_not_reach_the_whatwg_parse_path(raw: bytes) -> None:
    # scope boundary: the standalone detector reports the mark's own label, but parse() keeps the
    # spec-locked WHATWG name -- a UTF-8 mark stays UTF-8 and FF FE 00 00 stays UTF-16LE
    standalone = detect(raw).encoding
    parsed = parse(raw, detect_encoding=True).encoding
    assert standalone != parsed
    assert parsed in {"UTF-8", "UTF-16LE"}


def test_valid_utf8_is_certain() -> None:
    assert detect("café résumé Москва 日本語".encode()) == EncodingMatch("UTF-8", 1.0, None)


def test_iso_2022_jp_is_certain() -> None:
    raw = "こんにちは世界".encode("iso-2022-jp")
    assert detect(raw) == EncodingMatch("ISO-2022-JP", 1.0, "Japanese")


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
            "Η ελληνική γλώσσα είναι μία από τις αρχαιότερες γλώσσες στον κόσμο σήμερα εδώ.",  # noqa: RUF001
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
    assert match.encoding == encoding
    assert match.language == language
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
    assert detect_all(b"\xef\xbb\xbfhello") == [EncodingMatch("UTF-8-SIG", 1.0, None, bom=True)]


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
    visual = "שלום לכולם זהו משפט בעברית עם סימני פיסוק רבים, נקודות. ועוד!"[::-1].encode("iso-8859-8")
    matches = detect_all(visual)
    assert matches[0].encoding == "ISO-8859-8"
    assert matches[1].encoding == "windows-1255"
    assert matches[0].confidence == matches[1].confidence


def test_undecodable_bytes_fall_back_to_windows_1252_with_no_confidence() -> None:
    # 0x81 followed by a newline disqualifies every candidate, so the WHATWG windows-1252 default
    # is returned with confidence 0.0: there is no positive evidence for it
    assert detect(b"\x81\n") == EncodingMatch("windows-1252", 0.0, None)


def test_non_bytes_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        detect("text")  # ty: ignore[invalid-argument-type]  # str exposes no byte buffer


# Content-based language detection (roadmap #459) ports whatlang's trigram model (Method::Trigram): the samples below
# detect their language exactly, and the port reproduces whatlang bit-for-bit, agreeing with whatlang's own
# Method::Trigram on all 69 entries of whatlang's example corpus, confidence to 1e-6 included.
_LANGUAGE_CASES: list[tuple[str, str, str]] = [
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
    ("Та нічого, все нормально. А в тебе як справи сьогодні зранку, дорогий друже мій?", "ukr", "Cyrillic"),  # noqa: RUF001
    ("Η γλώσσα μας είναι πολύ όμορφη και έχει πλούσια και μεγάλη ιστορία στον κόσμο.", "ell", "Greek"),  # noqa: RUF001
    ("האקדמיה ללשון העברית היא המוסד העליון למדע הלשון העברית שנמצא היום בירושלים.", "heb", "Hebrew"),
    ("ككل حوالي ومعظم الناس يتحدثون هذه اللغة الجميلة في جميع أنحاء العالم اليوم بسعادة.", "ara", "Arabic"),
    ("हिमालयी वन में रहने वाली यह चिड़िया बहुत सुंदर होती है और यहाँ बहुत आम पाई जाती है।", "hin", "Devanagari"),
    ("北京是中国的首都也是全国的政治文化中心而且历史非常悠久经济发展十分迅速。", "cmn", "Mandarin"),
    ("これは日本語で書かれたテキストです。ひらがなとカタカナと漢字を一緒に使います。", "jpn", "Hiragana"),
    ("한국어는 매우 아름다운 언어이며 배우기 쉽고 아주 재미있는 언어입니다 정말로요.", "kor", "Hangul"),
    ("ภาษาไทยเป็นภาษาที่สวยงามและมีประวัติศาสตร์อันยาวนานมากในภูมิภาคนี้ครับ", "tha", "Thai"),
    ("ქართული ენა არის ერთ-ერთი უძველესი ენა მსოფლიოში და მას აქვს საკუთარი ანბანი.", "kat", "Georgian"),
]


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
    assert detect_language(text) == LanguageMatch(None, 0.0, None)


def test_short_input_is_low_confidence() -> None:
    # two short words cannot separate the Latin languages, so the winner comes back well under full confidence
    match = detect_language("por que")
    assert match.language is not None
    assert match.confidence < 0.1


def test_threshold_drops_a_low_confidence_result() -> None:
    assert detect_language("por que", LanguageDetection(threshold=0.5)) == LanguageMatch(None, 0.0, None)


def test_threshold_keeps_a_confident_result() -> None:
    match = detect_language("There is no reason not to learn a language today.", LanguageDetection(threshold=0.5))
    assert match.language == "eng"


def test_allowed_constrains_the_candidates() -> None:
    # Esperanto shares the Latin script with Ukrainian's Cyrillic; only Esperanto is a Latin candidate, so it wins
    assert detect_language("Mi ne scias!", LanguageDetection(allowed=frozenset({"epo", "ukr"}))).language == "epo"


def test_allowed_with_a_single_candidate_is_certain() -> None:
    # one surviving candidate has no runner-up to compare against, so its confidence is 1.0
    match = detect_language("Mi ne scias hodiaŭ!", LanguageDetection(allowed=frozenset({"epo"})))
    assert match == LanguageMatch("epo", 1.0, "Latin", "Esperanto")


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
    text = "Η γλώσσα μας είναι όμορφη"  # noqa: RUF001
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
    han = "北京是中国首都也是政治文化中心历史悠久发展迅速人口众多经济繁荣科技教育"
    match = detect_language(han + suffix)
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
    assert detect_language(passage) == LanguageMatch("eng", 1.0, "Latin", "English")


def test_distance_saturates_on_an_adversarial_text() -> None:
    # a long block of high-frequency nonsense trigrams pushes the real-language trigrams appended after it to
    # the tail of the frequency ranking, so their rank displacement drives one candidate's distance past the
    # 90000 ceiling and exercises the saturation clamp; the detector still returns a Latin verdict without error
    filler = " ".join("".join(word) for word in islice(product("bcdfghjklmnpqrstvwxz", repeat=4), 300))
    tail = "Đây là một đoạn văn bản tiếng Việt với nhiều dấu thanh khác nhau được viết ra hôm nay"
    match = detect_language(f"{(filler + ' ') * 6}{tail}")
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
