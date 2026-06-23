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
from turbohtml import Formatter

doc = turbohtml.parse("<article><h1>Tea</h1><p class=note>café &amp; cake</p></article>")
print([h.text for h in doc.find_all("h1")])  # ['Tea']
print(doc.select_one("p.note").text)  # café & cake
print(doc.select_one("p").serialize(formatter=Formatter.NAMED_ENTITIES))
# <p class="note">caf&eacute; &amp; cake</p>
```

turbohtml models text as real child nodes following the WHATWG DOM shape, so `node[i]` indexes children and attributes
are reached through `node.attrs`.

## Capabilities

| Task              | API                                                                                                    |
| ----------------- | ------------------------------------------------------------------------------------------------------ |
| Escape / unescape | `escape`, `unescape` — byte-for-byte with `html.escape`/`html.unescape`                                |
| Tokenize          | `tokenize`, `Tokenizer` — WHATWG streaming tokenizer with incremental `feed`/`close`                   |
| Parse             | `parse`, `parse_fragment`, `IncrementalParser` — encoding sniffing and source positions                |
| Query             | `find`/`find_all`, CSS `select`/`select_one`, XPath `xpath`/`xpath_one`                                |
| Serialize         | `serialize` with `Formatter` for escaping and `Minify` for whitespace                                  |
| Sanitize          | `sanitize` — allowlist scrub of untrusted HTML (the `bleach.clean` successor)                          |
| Linkify           | `linkify` — auto-link URLs and emails without touching existing links (the `bleach.linkify` successor) |
| Markdown          | `to_markdown` — GitHub-Flavored Markdown export                                                        |
| Plain text        | `to_text`, `to_annotated_text` — layout-aware text, optionally with `(start, end, label)` spans        |
| Extract           | `tables`, `structured_data` (JSON-LD / Microdata / OpenGraph), `article` (main content)                |
| Build / edit      | `Element`, `E`/`ElementMaker`, `unwrap`/`wrap`/`decompose`/`replace_with` and live `attrs`             |
| Migration         | `turbohtml.migration.*` — drop-in shims for `markupsafe` and template autoescaping                     |

## Performance

On an Apple M4 measured with [pyperf](https://pyperf.readthedocs.io), `parse` builds a full WHATWG tree 2–5× faster than
the C parsers [lxml](https://lxml.de) and [selectolax](https://github.com/rushter/selectolax) and 30–80× faster than
[BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/), and `tokenize` runs 9–15× faster than `html.parser`.
See the [performance page](https://turbohtml.readthedocs.io/en/latest/development/performance.html) for the full tables
and methodology.

## Design principles

turbohtml puts the hot path in C over a single bump-allocated arena, exposes one fully-typed Python name per concept,
conforms to the WHATWG HTML standard, is free-threading ready, and carries no native dependencies. The full list lives
in the [design principles](https://turbohtml.readthedocs.io/en/latest/#design-principles).

## Migration

turbohtml is a clean break, not an API-compatible replacement. The
[migration guides](https://turbohtml.readthedocs.io/en/latest/migration/) translate code from pandas, markupsafe,
BeautifulSoup, lxml, html5lib, the standard library, and 20+ other libraries, ordered by adoption.

## Documentation

Full documentation — tutorials, how-to guides, migration guides, the API reference, and the design rationale — lives at
[turbohtml.readthedocs.io](https://turbohtml.readthedocs.io).

## License

`turbohtml` is released under the [MIT license](LICENSE). </content> </invoke>
