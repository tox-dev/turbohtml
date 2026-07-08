# turbohtml

[![PyPI](https://img.shields.io/pypi/v/turbohtml)](https://pypi.org/project/turbohtml/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/turbohtml.svg)](https://pypi.org/project/turbohtml/)
[![Downloads](https://static.pepy.tech/badge/turbohtml/month)](https://pepy.tech/project/turbohtml)
[![Documentation status](https://readthedocs.org/projects/turbohtml/badge/?version=latest)](https://turbohtml.readthedocs.io/en/latest/?badge=latest)
[![check](https://github.com/tox-dev/turbohtml/actions/workflows/check.yaml/badge.svg)](https://github.com/tox-dev/turbohtml/actions/workflows/check.yaml)

A fast, fully typed HTML toolkit for Python with a C-accelerated core: tokenize, parse, query, edit, serialize, and
extract HTML several times faster than the pure-Python alternatives, with free-threading support. The hot path is C; a
thin typed facade is the only Python you touch. It is not a drop-in for the libraries it replaces.

## Install

```console
$ pip install turbohtml
```

Wheels ship per interpreter for CPython 3.10–3.15 (including free-threading), so there is nothing to compile.

## Quickstart

Parse a document, query it with a CSS selector, and serialize a node back to HTML with the escaping you choose:

```python
import turbohtml
from turbohtml import Formatter, Html

doc = turbohtml.parse("<article><h1>Tea</h1><p class=note>café &amp; cake</p></article>")
print([h.text for h in doc.find_all("h1")])  # ['Tea']
print(doc.select_one("p.note").text)  # café & cake
print(doc.select_one("p").serialize(Html(formatter=Formatter.NAMED_ENTITIES)))
# <p class="note">caf&eacute; &amp; cake</p>
```

Each renderer takes one configuration object — `Html` for `serialize`/`encode`, `Markdown` for `to_markdown`, and
`PlainText` for `to_text`/`to_annotated_text` — instead of a long keyword list, so options stay grouped and
discoverable.

turbohtml models text as real child nodes following the WHATWG DOM shape, so `node[i]` indexes children and attributes
are reached through `node.attrs`.

The dominant scraping workflow — isolate the article and hand it to a language model as Markdown — is two calls:

```python
import turbohtml

html = "<body><nav>Home</nav><article><h1>Tea</h1><p>Loose-leaf tea steeps best just off the boil.</p></article><aside>Ads</aside></body>"
doc = turbohtml.parse(html)
print(doc.main_content().to_markdown())
# # Tea
#
# Loose-leaf tea steeps best just off the boil.
```

`main_content` scores out the navigation and sidebars, and `to_markdown` renders the article, replacing a
readability-plus-markdownify pipeline with no intermediate string.

## Capabilities

| Task              | API                                                                                                    |
| ----------------- | ------------------------------------------------------------------------------------------------------ |
| Escape / unescape | `escape`, `unescape` — byte-for-byte with `html.escape`/`html.unescape`                                |
| Tokenize          | `tokenize`, `Tokenizer` — WHATWG streaming tokenizer with incremental `feed`/`close`                   |
| Parse             | `parse`, `parse_fragment`, `parse_xml`, `IncrementalParser` — encoding sniffing and source positions   |
| Detect            | `detect`, `detect_all` — standalone encoding detection (the `chardet`/`charset-normalizer` successor)  |
| Query             | `find`/`find_all`, CSS `select`/`select_one`, XPath `xpath`/`xpath_one`, `matches`/`closest`           |
| Computed style    | `computed_style` — resolve the CSS cascade to a computed value (CSSOM)                                 |
| Convert           | `css_to_xpath` — translate a CSS selector to XPath 1.0 (the `cssselect` successor)                     |
| Transform         | `transform.Transform` — apply an XSLT 1.0 stylesheet                                                   |
| Validate          | `validate.XMLSchema`, `RelaxNG`, and HTML5 authoring conformance checks                                |
| Serialize         | `serialize`/`encode` with an `Html` config (`Formatter` escaping, `Indent`/`Minify` whitespace)        |
| Minify            | `minify` (HTML), `minify_css`, `minify_js` — value-safe, and `Minify(minify_js=...)` for `<script>`    |
| Sanitize          | `sanitize` — allowlist scrub of untrusted HTML (the `bleach.clean` successor)                          |
| Linkify           | `linkify` — auto-link URLs and emails without touching existing links (the `bleach.linkify` successor) |
| Rewrite           | `rewrite.rewrite` — edit markup in one streaming pass, no tree (the `lol-html` successor)              |
| Forms             | `field_value`, `checked`, `form_data` — read and submit form controls with WHATWG semantics            |
| Markdown          | `to_markdown` with a `Markdown` config — GitHub-Flavored Markdown export                               |
| Plain text        | `to_text`, `to_annotated_text` with a `PlainText` config — layout-aware text, optional labeled spans   |
| Extract           | `tables`, `structured_data` (JSON-LD / Microdata / OpenGraph), `article` (main content), `feed`        |
| Build / edit      | `Element`, `E`/`ElementMaker`, `unwrap`/`wrap`/`decompose`/`replace_with` and live `attrs`             |
| Command line      | the `turbohtml` console script — `to-markdown`, `to-text`, `detect`, `minify`, `sanitize` over stdin   |
| Migration         | `turbohtml.migration.*` — drop-in shims for `markupsafe` and template autoescaping                     |

## Performance

On an Apple M4 measured with [pyperf](https://pyperf.readthedocs.io), `parse` builds a full WHATWG tree 2–5× faster than
the C parsers [lxml](https://lxml.de) and [selectolax](https://github.com/rushter/selectolax) and 30–80× faster than
[BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/), and `tokenize` runs 9–15× faster than `html.parser`.
See the [performance page](https://turbohtml.readthedocs.io/en/stable/development/performance.html) for the full tables
and methodology.

## Design principles

turbohtml puts the hot path in C over a single bump-allocated arena, exposes one fully-typed Python name per concept,
conforms to the WHATWG HTML standard, is free-threading ready, and carries no native dependencies. The full list lives
in the [design principles](https://turbohtml.readthedocs.io/en/stable/#design-principles).

## Migration

turbohtml is a clean break, not an API-compatible replacement. The
[migration guides](https://turbohtml.readthedocs.io/en/stable/migration/) translate code from 65 libraries —
BeautifulSoup, lxml, html5lib, pandas, markupsafe, and the standard library among them — each mapped to the namespace
that replaces it, ordered by adoption.

## Documentation

Full documentation — tutorials, how-to guides, migration guides, the API reference, and the design rationale — lives at
[turbohtml.readthedocs.io](https://turbohtml.readthedocs.io).

## License

`turbohtml` is released under the [MIT license](LICENSE).
