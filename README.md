# turbohtml

[![PyPI](https://img.shields.io/pypi/v/turbohtml)](https://pypi.org/project/turbohtml/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/turbohtml.svg)](https://pypi.org/project/turbohtml/)
[![Downloads](https://static.pepy.tech/badge/turbohtml/month)](https://pepy.tech/project/turbohtml)
[![Documentation status](https://readthedocs.org/projects/turbohtml/badge/?version=latest)](https://turbohtml.readthedocs.io/en/latest/?badge=latest)
[![check](https://github.com/tox-dev/turbohtml/actions/workflows/check.yaml/badge.svg)](https://github.com/tox-dev/turbohtml/actions/workflows/check.yaml)

A fast, fully typed HTML toolkit for Python with a C-accelerated core. turbohtml escapes and unescapes HTML to match the
standard library byte for byte, tokenizes markup with a WHATWG-conformant streaming tokenizer, and parses whole
documents into a navigable element tree you query with CSS selectors, edit in place, build from scratch, serialize back
to conformant HTML, and export to GitHub-Flavored Markdown or layout-aware plain text. A
[markupsafe](https://markupsafe.palletsprojects.com)-compatible `turbohtml.markup` covers template autoescaping, and
`turbohtml.linkify` auto-links URLs and emails the way [bleach](https://github.com/mozilla/bleach) did. Each operation
runs several times faster than its pure-Python counterpart and supports the free-threaded build.

## Install

```console
$ pip install turbohtml
```

Wheels ship per interpreter for CPython 3.10–3.15 (including free-threading), so there is nothing to compile.

## Usage

Escape text before interpolating it into HTML so it cannot break out of its context:

```python
import turbohtml

print(turbohtml.escape('<a href="?x=1&y=2">Tom & Jerry</a>'))
# &lt;a href=&quot;?x=1&amp;y=2&quot;&gt;Tom &amp; Jerry&lt;/a&gt;
```

Inside a text node the quotes are safe, so pass `quote=False` to keep the output smaller:

```python
print(turbohtml.escape('He said "hi" & left', quote=False))
# He said "hi" &amp; left
```

Turn HTML character references back into text, following the full HTML5 rules (named, numeric, and longest-match
references that omit the trailing semicolon):

```python
print(turbohtml.unescape("caf&eacute; &amp; r&eacute;sum&eacute; &#127881;"))
# café & résumé 🎉
```

`escape` and `unescape` reproduce `html.escape` and `html.unescape` exactly, so turbohtml is a drop-in replacement on
hot paths.

For template output, `turbohtml.markup` is a markupsafe drop-in: `Markup` marks trusted HTML, and combining it with
untrusted values escapes them. Swap `from markupsafe import ...` for `from turbohtml.markup import ...`:

```python
from turbohtml.markup import Markup, escape

print(Markup("<li>{}</li>").format("<script>alert(1)</script>"))
# <li>&lt;script&gt;alert(1)&lt;/script&gt;</li>
print(escape("Tom & Jerry"))
# Tom &amp; Jerry
```

`turbohtml.linkify` replaces `bleach.linkify`, which has no other successor now that bleach is end of life. It parses
the HTML first, so it never links inside an existing `<a>`, a `<script>`, or a tag you skip:

```python
from turbohtml.linkify import linkify

print(linkify("email bob@example.com or visit https://example.com", parse_email=True))
# email <a href="mailto:bob@example.com">bob@example.com</a> or visit <a href="https://example.com" rel="nofollow">https://example.com</a>
```

Tokenize markup into a stream of tokens that follows the WHATWG tokenization algorithm:

```python
for token in turbohtml.tokenize('<p class="x">Tom &amp; Jerry</p>'):
    print(token.type.name, token.tag or token.data, token.attrs)
# START_TAG p [('class', 'x')]
# TEXT Tom & Jerry None
# END_TAG p []
```

For incremental input, `Tokenizer.feed()` returns the tokens completed by each chunk and `close()` flushes the rest:

```python
tokenizer = turbohtml.Tokenizer()
print([token.tag for token in tokenizer.feed("<div><sp")])  # ['div']
print([token.tag for token in tokenizer.feed("an>")])  # ['span']
print(list(tokenizer.close()))  # []
```

Parse a whole document into a tree and walk it with `find`, `find_all`, and the navigation accessors:

```python
doc = turbohtml.parse("<ul><li>one<li>two</ul>")
print([li.text for li in doc.find_all("li")])  # ['one', 'two']
print(doc.find("ul").children[0].tag)  # li
```

Query with a CSS selector, and serialize a node back to HTML with the escaping you choose:

```python
from turbohtml import Formatter

doc = turbohtml.parse("<article><h1>Tea</h1><p class=note>café &amp; cake</p></article>")
print(doc.select_one("p.note").text)
# café & cake
print(doc.select_one("p").serialize(formatter=Formatter.NAMED_ENTITIES))
# <p class="note">caf&eacute; &amp; cake</p>
```

Export a node to GitHub-Flavored Markdown, the `scrape` → `Markdown` step that needed html2text or markdownify:

```python
doc = turbohtml.parse("<h1>Tea</h1><p>Steep <b>green</b> tea.</p><ul><li>cup</li><li>water</li></ul>")
print(doc.to_markdown())
# # Tea
#
# Steep **green** tea.
#
# - cup
# - water
```

The keyword options cover the markdownify and html2text surface; `google_doc=True` adds html2text's Google-Docs mode,
reading the inline-CSS styling such an export carries.

Or to layout-aware plain text (the `inscriptis` role), with tables laid out as aligned columns:

```python
doc = turbohtml.parse("<table><tr><th>Item</th><th>Qty</th></tr><tr><td>Apples</td><td>3</td></tr></table>")
print(doc.to_text())
# Item    Qty
# Apples  3
```

`to_annotated_text` returns that text with `(start, end, label)` spans for elements matching an `annotation_rules`
mapping, the inscriptis annotation role:

```python
doc = turbohtml.parse("<h1>Q3</h1><p>Up <b>12%</b></p>")
text, labels = doc.to_annotated_text({"h1": ["heading"], "b": ["metric"]})
# ("Q3\n\nUp 12%", [(0, 2, "heading"), (6, 9, "metric")])
```

Pass `bytes` to sniff the encoding the WHATWG way (byte-order mark, then a `<meta>` declaration):

```python
doc = turbohtml.parse(b'<meta charset="iso-8859-2"><p>\xe1</p>')
print((doc.encoding, doc.find("p").text))  # ('iso-8859-2', 'á')
```

Parse a fragment as the contents of a context element, the way `innerHTML` does:

```python
cell = turbohtml.parse_fragment("<td>data", context="tr")
print((cell.tag, cell.text))  # ('tr', 'data')
```

Build a tree from scratch with the node constructors, then assemble it (a list value for a token-list attribute like
`class` joins on a space, and the `text` setter fills an element with a single text child):

```python
from turbohtml import Element

card = Element("article", {"class": ["card", "lg"]})
heading = Element("h2")
heading.text = "Tea"
card.append(heading)
print(card.html)
# <article class="card lg"><h2>Tea</h2></article>
```

Edit a parsed tree in place. `unwrap`, `decompose`, `wrap`, `insert_before`, `replace_with`, and the rest move nodes
within a tree or adopt them from another, and `element.attrs` is a live mapping you assign to:

```python
doc = turbohtml.parse("<p>keep <b>bold</b> <span>drop</span></p>")
doc.find("b").unwrap()
doc.find("span").decompose()
doc.find("p").attrs["class"] = "lead"
print(doc.find("p").html)
# <p class="lead">keep bold </p>
```

The sealed node hierarchy (`Element`, `Text`, `Comment`, `Doctype`, `ProcessingInstruction`, `CData`, and `Document`)
sets `__match_args__` for structural pattern matching, and any node deep-copies with `copy.copy`, `copy.deepcopy`, or
`pickle`.

## Performance

turbohtml's C core makes every operation several times faster than its pure-Python counterpart, and it runs faster than
the other C libraries on the read-path benchmarks. Measured with [pyperf](https://pyperf.readthedocs.io) on an Apple M4:

- `escape` and `unescape` match the standard library byte for byte while running several times faster, up to 22× on
  no-op text and 13× on entity-dense input.
- `turbohtml.markup.escape` matches markupsafe and runs 2–3× faster on the small strings template autoescaping escapes.
- `turbohtml.linkify` auto-links HTML 5–20× faster than bleach and 6–11× faster than the plain-text
  [linkify-it-py](https://github.com/tsutsu3/linkify-it-py) scanner, which only finds links without rewriting them.
- `tokenize` is 9–16× faster than `html.parser` wherever markup appears.
- `parse` builds a full WHATWG tree 2–5× faster than the C parsers [lxml](https://lxml.de) and
  [selectolax](https://github.com/rushter/selectolax), and 30–80× faster than the pure-Python
  [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) and
  [html5lib](https://github.com/html5lib/html5lib-python).
- `find_all` and CSS `select` run 2–40× faster than lxml's C XPath and [cssselect](https://github.com/scrapy/cssselect)
  at every size and 100× faster than BeautifulSoup.
- serializing a tree back to HTML runs 2–4× faster than lxml and selectolax and about 40× faster than BeautifulSoup.
- `to_markdown` exports GitHub-Flavored Markdown 40–110× faster than markdownify and html2text, which build and convert
  in Python.
- `to_text` renders layout-aware plain text 20–35× faster than [inscriptis](https://github.com/weblyzard/inscriptis).
- building a tree from scratch and editing a parsed one both run about twice as fast as lxml and an order of magnitude
  faster than BeautifulSoup.

See the [performance page](https://turbohtml.readthedocs.io/en/latest/performance.html) for the full sectioned tables
and the methodology.

## Documentation

Full documentation, including tutorials, how-to guides, migration guides from BeautifulSoup, lxml, selectolax, html5lib,
and the standard library, the API reference, and the design rationale, lives at
[turbohtml.readthedocs.io](https://turbohtml.readthedocs.io).

## License

`turbohtml` is released under the [MIT license](LICENSE).
