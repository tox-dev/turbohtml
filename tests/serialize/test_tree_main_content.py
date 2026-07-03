"""Main-content extraction and article harvesting via the readability heuristic.

``Node.main_content()``/``main_text()`` play the resiliparse role; ``Node.article()``
builds on the same content scoring and adds the trafilatura / newspaper3k metadata
record. The readability content-density heuristic is pure C in
``treebuilder_readability.h``. These cases drive every scoring branch with
article-shaped fixtures (tag and class/id weighting, boilerplate prunings,
link-density discounting, candidate-array regrow, empty-result guards) and every
metadata field beside it (title, byline, date, description and lang, each in its
present, absent, and multiple-source-precedence form).
"""

from __future__ import annotations

import pytest

from turbohtml import Article, Document, Element, Text, parse, parse_fragment

# A prose paragraph: >100 chars so the length bonus saturates, with commas that
# each add a clause point, the signal that marks it as real content.
PROSE = (
    "A comet is an icy small body that, when it passes close to the Sun, warms up, "
    "begins to release gases, and forms a glowing coma, a thin atmosphere, around it."
)
# A shorter prose paragraph (>25 chars, <100) so the length bonus stays below the cap.
SHORT_PROSE = "The tail points away from the Sun."

# A very long paragraph (>300 chars) so the length bonus saturates at the cap.
LONG_PROSE = PROSE + " " + PROSE + " " + PROSE

# A div/list-structured body that holds no <p>/<td>/<pre> to score: the visible text
# lives in <li>, the shape the semantic-container fallback has to rescue.
LIST_ITEM = "<li><strong>Elf Modelle sind empfehlenswert in punkto Sicherheit.</strong></li>"

# The minimal content body the article() metadata cases hang their head/body fixtures off.
BODY = f"<article class=post><p>{PROSE}</p></article>"


def main_tag(html: str) -> str | None:
    found = parse(html).main_content()
    return None if found is None else found.tag


def list_body(items: int) -> str:
    return f"<div class=text><ul>{LIST_ITEM * items}</ul></div>"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            f"<nav><a href='/'>Home</a></nav><article class=post><p>{PROSE}</p></article>"
            f"<footer><p>Copyright notice, all rights reserved here.</p></footer>",
            "article",
            id="article-beats-nav-and-footer",
        ),
        pytest.param(
            f"<div id=main-content><p>{PROSE}</p><p>{PROSE}</p></div><div class=sidebar><p>{PROSE}</p></div>",
            "div",
            id="content-id-beats-sidebar-class",
        ),
        pytest.param(
            f"<main><div class=entry><p>{PROSE}</p></div></main>",
            "div",
            id="positive-inner-div-wins",
        ),
    ],
)
def test_main_content_selects_article(html: str, expected: str) -> None:
    assert main_tag(html) == expected


def test_main_content_returns_element() -> None:
    found = parse(f"<article class=post><p>{PROSE}</p></article>").main_content()
    assert isinstance(found, Element)
    assert found.attrs["class"] == ["post"]


def test_main_text_renders_the_winner() -> None:
    text = parse(
        f"<nav><a href='/'>Home</a></nav><article class=post><h1>Comets</h1><p>{PROSE}</p></article>"
    ).main_text()
    assert text.startswith("Comets")
    assert "A comet is an icy" in text
    assert "Home" not in text


@pytest.mark.parametrize(
    "html",
    [
        pytest.param("", id="empty"),
        pytest.param("<nav><a href='/'>Home</a></nav>", id="navigation-only"),
        pytest.param("<p>Too short.</p><p>Also brief.</p>", id="only-short-paragraphs"),
        pytest.param(f"<div class=comment><p>{PROSE}</p></div>", id="unlikely-subtree-skipped"),
    ],
)
def test_no_main_content(html: str) -> None:
    doc = parse(html)
    assert doc.main_content() is None
    assert not doc.main_text()


def test_unlikely_subtree_rescued_by_maybe_hint() -> None:
    assert main_tag(f"<div class='comment main'><p>{PROSE}</p></div>") == "div"


def test_negative_class_loses_to_neutral_container() -> None:
    html = f"<section><p>{PROSE}</p><p>{PROSE}</p></section><div class=widget><p>{PROSE}</p><p>{PROSE}</p></div>"
    assert main_tag(html) == "section"


def test_uppercase_positive_class_matches() -> None:
    assert main_tag(f"<div class=ARTICLE><p>{PROSE}</p></div>") == "div"


def test_match_by_id_when_no_class() -> None:
    assert main_tag(f"<div id=story><p>{PROSE}</p></div>") == "div"


def test_valueless_class_attribute_is_ignored() -> None:
    assert main_tag(f"<div class id=post><p>{PROSE}</p></div>") == "div"


def test_grandparent_is_none_for_direct_paragraph_parent() -> None:
    div = parse(f"<div><p>{PROSE}</p></div>").find("div")
    assert isinstance(div, Element)
    found = div.main_content()
    assert isinstance(found, Element)
    assert found.tag == "div"


def test_score_propagates_to_grandparent() -> None:
    found = parse(f"<main><article><p>{PROSE}</p></article></main>").main_content()
    assert isinstance(found, Element)
    assert found.tag == "article"


@pytest.mark.parametrize(
    "wrapper",
    [
        pytest.param("blockquote", id="blockquote-plus-three"),
        pytest.param("li", id="li-minus-three"),
        pytest.param("form", id="form-minus-three"),
    ],
)
def test_tag_weight_containers_still_win_with_strong_prose(wrapper: str) -> None:
    html = f"<{wrapper}><p>{PROSE}</p><p>{PROSE}</p></{wrapper}>"
    assert main_tag(html) == wrapper


def test_table_cell_paragraph_scores() -> None:
    # A <td> is itself a paragraph tag, so its combined text rolls up to the <tr>,
    # which outscores the cell; the cell is still seeded as a candidate (tag weight +3).
    html = f"<table><tr><td><p>{PROSE}</p><p>{PROSE}</p></td></tr></table>"
    assert main_tag(html) == "tr"


def test_heading_cell_negative_weight_can_still_win() -> None:
    html = f"<table><tr><th><p>{PROSE}</p><p>{PROSE}</p></th></tr></table>"
    assert main_tag(html) == "th"


def test_high_link_density_candidate_is_discounted_to_nothing() -> None:
    long_link = "Read the full story, the complete report, and the appendix, all linked over here now"
    assert parse(f"<div><p><a href='/x'>{long_link}</a></p></div>").main_content() is None


def test_link_text_lowers_but_does_not_eliminate_prose() -> None:
    html = f"<article class=post><p>{PROSE} <a href='/u'>see more</a></p><p>{PROSE}</p></article>"
    assert main_tag(html) == "article"


def test_foreign_namespace_subtree_is_skipped() -> None:
    html = (
        "<svg><text>vector caption text goes here in the drawing</text></svg>"
        f"<article class=post><p>{PROSE}</p></article>"
    )
    assert main_tag(html) == "article"


def test_embedded_skip_tags_do_not_count_as_text() -> None:
    html = (
        "<article class=post><p>Real prose, with a clause, sits beside noise here, plenty of words to count."
        "<script>var junk = 1;</script><svg><text>vector</text></svg></p></article>"
    )
    found = parse(html).main_content()
    assert isinstance(found, Element)
    assert "var junk" not in found.main_text()


def test_comment_and_nested_anchor_markup_inside_prose() -> None:
    html = (
        f"<article class=post><p>{PROSE}<!--editor note--> and a link "
        f"<a href='/u'>to the <b>full</b> report here, with details</a></p><p>{PROSE}</p></article>"
    )
    found = parse(html).main_content()
    assert isinstance(found, Element)
    assert found.tag == "article"
    assert "editor note" not in found.main_text()


def test_empty_text_node_counts_as_zero_length() -> None:
    article = Element("article", {"class": "post"})
    paragraph = Element("p")
    paragraph.append(Text(PROSE))
    paragraph.append(Text(""))
    article.append(paragraph)
    found = article.main_content()
    assert isinstance(found, Element)
    assert found.tag == "article"


def test_short_paragraph_below_threshold_is_noise() -> None:
    html = f"<div class=post><p>tiny</p></div><div class=entry><p>{PROSE}</p><p>{PROSE}</p></div>"
    found = parse(html).main_content()
    assert isinstance(found, Element)
    assert found.attrs["class"] == ["entry"]


def test_short_prose_scores_without_length_bonus() -> None:
    assert main_tag(f"<article class=post><p>{SHORT_PROSE}</p><p>{SHORT_PROSE}</p></article>") == "article"


def test_very_long_paragraph_saturates_the_length_bonus() -> None:
    assert main_tag(f"<article class=post><p>{LONG_PROSE}</p></article>") == "article"


def test_many_candidates_grow_the_array() -> None:
    sections = "".join(f"<section><div class=entry><p>{PROSE}</p></div></section>" for _ in range(8))
    found = parse(f"<body>{sections}</body>").main_content()
    assert isinstance(found, Element)
    assert found.tag in {"div", "section"}


def test_methods_exist_on_document_and_element() -> None:
    doc = parse(f"<article class=post><p>{PROSE}</p></article>")
    assert isinstance(doc, Document)
    body = doc.find("body")
    assert isinstance(body, Element)
    assert body.main_content() is not None


def test_div_list_body_without_paragraphs_falls_back_to_article() -> None:
    # #385: an <article> whose text lives in div/ul/li with no scoring <p> must not
    # extract to empty; the semantic-container fallback surfaces the <article>.
    html = f"<html lang=de><body><main><article><h1>Test</h1>{list_body(12)}</article></main></body></html>"
    found = parse(html).main_content()
    assert isinstance(found, Element)
    assert found.tag == "article"
    assert "empfehlenswert" in found.text


def test_semantic_fallback_ignores_a_thin_container() -> None:
    # A container below the visible-text floor is not a body; extraction stays empty.
    assert parse("<article><ul><li>tiny</li></ul></article>").main_content() is None


def test_semantic_fallback_uses_main_when_no_article() -> None:
    found = parse(f"<body><main>{list_body(12)}</main></body>").main_content()
    assert isinstance(found, Element)
    assert found.tag == "main"


def test_semantic_fallback_prefers_the_richest_article() -> None:
    # Two paragraph-less articles both clear the floor; the one with more text wins,
    # and <article> is preferred over the enclosing structure.
    html = f"<body><article>{list_body(12)}</article><article>{list_body(5)}</article></body>"
    found = parse(html).main_content()
    assert isinstance(found, Element)
    assert len(found.find_all("li")) == 12


def test_body_class_with_unlikely_substring_is_not_stripped() -> None:
    # #399: a body-level state class carrying an unlikely substring (has-localnav)
    # must not zero the whole page; the multi-paragraph body still extracts.
    body = f'<div class="content__article-body">{f"<p>{PROSE}</p>" * 4}</div>'
    found = parse(f'<html><body class="has-localnav">{body}</body></html>').main_content()
    assert isinstance(found, Element)
    assert found.tag == "div"
    assert found.attrs["class"] == ["content__article-body"]


def test_landmark_only_body_is_rescued_on_retry() -> None:
    # #411: a body living solely inside a <footer> must not be discarded; the retry
    # keeps landmarks so the footer's paragraphs score.
    found = parse(f"<body><footer class=content>{f'<p>{PROSE}</p>' * 4}</footer></body>").main_content()
    assert isinstance(found, Element)
    assert found.tag == "footer"
    assert "A comet is an icy" in found.text


# === article(): the content body plus harvested metadata ===================
#
# article() reuses the content scoring above and adds the page-metadata record.
# The per-field precedence cases below share the BODY content fixture so each one
# isolates exactly the head/body markup that feeds one field.


def test_article_returns_article_record() -> None:
    art = parse(f"<html lang=en><body>{BODY}</body></html>").article()
    assert isinstance(art, Article)
    assert isinstance(art.element, Element)
    assert art.element.tag == "article"
    assert "A comet is an icy" in art.text
    assert art.lang == "en"


def test_article_no_content_yields_none_element_and_empty_text() -> None:
    art = parse("<title>Just a title</title><p>Too short.</p>").article()
    assert art.element is None
    assert not art.text
    assert art.title == "Just a title"


def test_article_metadata_present_without_content() -> None:
    art = parse("<html lang=fr><head><title>Solo</title></head><body><p>brief</p></body></html>").article()
    assert art.element is None
    assert art.title == "Solo"
    assert art.lang == "fr"


def test_article_all_fields_absent_are_none() -> None:
    art = parse(f"<body>{BODY}</body>").article()
    assert art.title is None
    assert art.byline is None
    assert art.date is None
    assert art.description is None


def test_article_available_on_element() -> None:
    body = parse(f"<html lang=en><body>{BODY}</body></html>").find("body")
    assert isinstance(body, Element)
    art = body.article()
    assert isinstance(art.element, Element)
    assert art.element.tag == "article"


def test_article_programmatic_element_without_html_has_no_lang() -> None:
    article = Element("article", {"class": "post"})
    paragraph = Element("p")
    paragraph.append(Element("span"))
    article.append(paragraph)
    art = article.article()
    assert art.lang is None
    assert art.title is None


@pytest.mark.parametrize(
    ("head", "body", "expected"),
    [
        pytest.param(
            "<title>Page</title><meta property=og:title content='Social'>",
            "<h1>Heading</h1>",
            "Heading",
            id="h1-wins",
        ),
        pytest.param(
            "<title>Page</title><meta property=og:title content='Social'>",
            "",
            "Social",
            id="og-title-when-no-h1",
        ),
        pytest.param("<title>Page</title>", "", "Page", id="title-element-last"),
        pytest.param(
            "<meta property=og:title content='Social'>",
            "<h1>   </h1>",
            "Social",
            id="blank-h1-falls-through",
        ),
    ],
)
def test_article_title_precedence(head: str, body: str, expected: str) -> None:
    art = parse(f"<html><head>{head}</head><body>{body}{BODY}</body></html>").article()
    assert art.title == expected


@pytest.mark.parametrize(
    ("head", "body", "expected"),
    [
        pytest.param(
            "<meta name=author content='Meta Author'>",
            "<a rel='nofollow author' href='/u'>Link Author</a>",
            "Link Author",
            id="rel-author-link-wins",
        ),
        pytest.param(
            "<meta name=author content='Meta Author'><meta property=article:author content='Article Author'>",
            "",
            "Meta Author",
            id="meta-author-when-no-link",
        ),
        pytest.param(
            "<meta property=article:author content='Article Author'>",
            "",
            "Article Author",
            id="article-author-last",
        ),
    ],
)
def test_article_byline_precedence(head: str, body: str, expected: str) -> None:
    art = parse(f"<html><head>{head}</head><body>{body}{BODY}</body></html>").article()
    assert art.byline == expected


def test_article_byline_ignores_non_author_rel() -> None:
    art = parse(f"<body><a rel='nofollow noopener ' href='/x'>Nope</a>{BODY}</body>").article()
    assert art.byline is None


def test_article_rel_author_with_leading_whitespace_matches() -> None:
    art = parse(f"<body><a rel=' author' href='/u'>Padded</a>{BODY}</body>").article()
    assert art.byline == "Padded"


def test_article_byline_skips_anchor_without_rel() -> None:
    art = parse(f"<head><meta name=author content='Meta'></head><body><a href='/x'>Plain</a>{BODY}</body>").article()
    assert art.byline == "Meta"


def test_article_empty_rel_author_link_falls_through_to_meta() -> None:
    art = parse(
        f"<head><meta name=author content='Meta'></head><body><a rel=author href='/u'></a>{BODY}</body>"
    ).article()
    assert art.byline == "Meta"


@pytest.mark.parametrize(
    ("head", "body", "expected"),
    [
        pytest.param(
            "<meta property=article:published_time content='2020-01-01'>",
            "<time datetime='2024-05-06'>May 6</time>",
            "2024-05-06",
            id="time-datetime-wins",
        ),
        pytest.param("", "<time>March 2024</time>", "March 2024", id="time-text-when-no-datetime"),
        pytest.param(
            "<meta property=article:published_time content='2020-01-01'>",
            "",
            "2020-01-01",
            id="published-time-meta",
        ),
        pytest.param("<meta name=date content='2019-12-31'>", "", "2019-12-31", id="common-date-meta"),
        pytest.param("<meta name=pubdate content='2018-11-30'>", "", "2018-11-30", id="pubdate-meta"),
        pytest.param("<meta name=dc.date content='2017-10-29'>", "", "2017-10-29", id="dc-date-meta"),
    ],
)
def test_article_date_precedence(head: str, body: str, expected: str) -> None:
    art = parse(f"<html><head>{head}</head><body>{body}{BODY}</body></html>").article()
    assert art.date == expected


def test_article_valueless_datetime_falls_back_to_time_text() -> None:
    art = parse(f"<body><time datetime>April 2024</time>{BODY}</body>").article()
    assert art.date == "April 2024"


def test_article_date_empty_time_falls_through_to_meta() -> None:
    html = (
        f"<html><head><meta name=date content='2016-01-01'></head><body><time datetime=''> </time>{BODY}</body></html>"
    )
    assert parse(html).article().date == "2016-01-01"


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        pytest.param(
            "<meta name=description content='Plain desc'><meta property=og:description content='OG desc'>",
            "OG desc",
            id="og-description-wins",
        ),
        pytest.param("<meta name=description content='Plain desc'>", "Plain desc", id="meta-description-fallback"),
    ],
)
def test_article_description_precedence(head: str, expected: str) -> None:
    art = parse(f"<html><head>{head}</head><body>{BODY}</body></html>").article()
    assert art.description == expected


def test_article_description_absent_is_none() -> None:
    assert parse(f"<body>{BODY}</body>").article().description is None


def test_article_lang_present() -> None:
    assert parse(f"<html lang='en-US'><body>{BODY}</body></html>").article().lang == "en-US"


def test_article_lang_absent_is_none() -> None:
    assert parse(f"<html><body>{BODY}</body></html>").article().lang is None


def test_article_valueless_lang_attribute_is_absent() -> None:
    assert parse(f"<html lang><body>{BODY}</body></html>").article().lang is None


def test_article_fragment_without_html_element_has_no_lang() -> None:
    fragment = parse_fragment(f"<article class=post><p>{PROSE}</p></article>", "div")
    art = fragment.article()
    assert isinstance(art.element, Element)
    assert art.lang is None


def test_article_whitespace_is_collapsed_and_trimmed() -> None:
    art = parse(f"<body><h1>\n  Comets\t and\f  Tails  \n</h1>{BODY}</body>").article()
    assert art.title == "Comets and Tails"


def test_article_foreign_elements_and_valueless_meta_are_skipped() -> None:
    html = (
        "<html lang=en><head><meta name>"
        "<meta property=og:title content='Real'>"
        "<meta property=og:description content='Desc'></head>"
        f"<body><svg><title>vector</title><a>link</a></svg>{BODY}</body></html>"
    )
    art = parse(html).article()
    assert art.title == "Real"
    assert art.description == "Desc"
    assert art.byline is None


def test_article_metadata_keys_match_case_insensitively() -> None:
    html = (
        "<head><meta property='OG:TITLE' content='Cased'></head>"
        f"<body><a rel='AUTHOR' href='/u'>Cased Author</a>{BODY}</body>"
    )
    art = parse(html).article()
    assert art.title == "Cased"
    assert art.byline == "Cased Author"


def test_article_meta_property_and_name_both_match() -> None:
    by_property = parse(f"<head><meta property=description content='via property'></head><body>{BODY}</body>")
    by_name = parse(f"<head><meta name=description content='via name'></head><body>{BODY}</body>")
    assert by_property.article().description == "via property"
    assert by_name.article().description == "via name"


def test_article_meta_without_content_attribute_is_skipped() -> None:
    html = f"<html><head><meta property=og:title><title>Real</title></head><body>{BODY}</body></html>"
    assert parse(html).article().title == "Real"


def test_article_meta_with_valueless_content_is_skipped() -> None:
    html = f"<html><head><meta property=og:title content><title>Fallback</title></head><body>{BODY}</body></html>"
    assert parse(html).article().title == "Fallback"


def test_article_fields_unpack_in_order() -> None:
    art = parse(f"<html lang=en><body>{BODY}</body></html>").article()
    element, text, title, byline, date, description, lang = art
    assert element is art.element
    assert text is art.text
    assert (title, byline, date, description, lang) == (
        art.title,
        art.byline,
        art.date,
        art.description,
        art.lang,
    )


def test_article_document_and_element_agree_on_metadata() -> None:
    doc = parse(f"<html lang=en><head><title>T</title></head><body>{BODY}</body></html>")
    assert isinstance(doc, Document)
    body = doc.find("body")
    assert isinstance(body, Element)
    assert doc.article().lang == body.article().lang == "en"
