# turbohtml

[![PyPI](https://img.shields.io/pypi/v/turbohtml)](https://pypi.org/project/turbohtml/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/turbohtml.svg)](https://pypi.org/project/turbohtml/)
[![Downloads](https://static.pepy.tech/badge/turbohtml/month)](https://pepy.tech/project/turbohtml)
[![Documentation status](https://readthedocs.org/projects/turbohtml/badge/?version=latest)](https://turbohtml.readthedocs.io/en/latest/?badge=latest)
[![check](https://github.com/tox-dev/turbohtml/actions/workflows/check.yaml/badge.svg)](https://github.com/tox-dev/turbohtml/actions/workflows/check.yaml)

A fast, fully typed HTML toolkit for Python with a C-accelerated core. turbohtml escapes and unescapes HTML to match the
standard library byte for byte, tokenizes markup with a WHATWG-conformant streaming tokenizer, and parses whole
documents into a navigable element tree. Each operation runs several times faster than its pure-Python counterpart and
supports the free-threaded build.

## Install

```console
$ pip install turbohtml
```

Wheels ship per interpreter for CPython 3.10–3.15 (including free-threading), so there is nothing to compile.

## Usage

Escape text before interpolating it into HTML so it cannot break out of its context:

```pycon
>>> import turbohtml
>>> turbohtml.escape('<a href="?x=1&y=2">Tom & Jerry</a>')
'&lt;a href=&quot;?x=1&amp;y=2&quot;&gt;Tom &amp; Jerry&lt;/a&gt;'
```

Inside a text node the quotes are safe, so pass `quote=False` to keep the output smaller:

```pycon
>>> turbohtml.escape('He said "hi" & left', quote=False)
'He said "hi" &amp; left'
```

Turn HTML character references back into text, following the full HTML5 rules (named, numeric, and longest-match
references that omit the trailing semicolon):

```pycon
>>> turbohtml.unescape("caf&eacute; &amp; r&eacute;sum&eacute; &#127881;")
'café & résumé 🎉'
```

`escape` and `unescape` reproduce `html.escape` and `html.unescape` exactly, so turbohtml is a drop-in replacement on
hot paths.

Tokenize markup into a stream of tokens that follows the WHATWG tokenization algorithm:

```pycon
>>> for token in turbohtml.tokenize('<p class="x">Tom &amp; Jerry</p>'):
...     print(token.type.name, token.tag or token.data, token.attrs)
START_TAG p [('class', 'x')]
TEXT Tom & Jerry None
END_TAG p []
```

For incremental input, `Tokenizer.feed()` returns the tokens completed by each chunk and `close()` flushes the rest:

```pycon
>>> tokenizer = turbohtml.Tokenizer()
>>> [token.tag for token in tokenizer.feed("<div><sp")]
['div']
>>> [token.tag for token in tokenizer.feed("an>")]
['span']
>>> list(tokenizer.close())
[]
```

Parse a whole document into a tree and walk it with `find`, `find_all`, and the navigation accessors:

```pycon
>>> doc = turbohtml.parse('<ul><li>one<li>two</ul>')
>>> [li.text for li in doc.find_all('li')]
['one', 'two']
>>> doc.find('ul').children[0].tag
'li'
```

Parse a fragment as the contents of a context element, the way `innerHTML` does:

```pycon
>>> cell = turbohtml.parse_fragment('<td>data', context='tr')
>>> cell.tag, cell.text
('tr', 'data')
```

## Performance

Measured with [pyperf](https://pyperf.readthedocs.io) on CPython 3.14.6 (release build) on an Apple M4 running macOS
26.5. The corpus cases are real documents: [Project Gutenberg's *War and Peace*](https://www.gutenberg.org/ebooks/2600),
the [WHATWG HTML specification source](https://github.com/whatwg/html/blob/main/source), the
[ECMAScript specification](https://github.com/tc39/ecma262), and a sample of
[web-platform-tests](https://github.com/web-platform-tests/wpt) pages.

`escape` runs against the standard library's [`html.escape`](https://docs.python.org/3/library/html.html#html.escape):

| input                    | turbohtml | html.escape |
| ------------------------ | --------- | ----------- |
| tiny plain (64 B)        | 0.04 µs   | 0.11 µs     |
| medium markup (4 KiB)    | 2.25 µs   | 7.17 µs     |
| no-op prose (4 MiB)      | 0.11 ms   | 2.51 ms     |
| book text (3 MiB)        | 0.66 ms   | 2.56 ms     |
| book HTML (4 MiB)        | 1.25 ms   | 4.54 ms     |
| spec HTML, dense (4 MiB) | 4.93 ms   | 12.8 ms     |
| UCS-2 plain (4 MiB)      | 0.70 ms   | 2.41 ms     |
| UCS-2 markup (4 MiB)     | 3.33 ms   | 10.9 ms     |
| UCS-4 plain (4 MiB)      | 0.91 ms   | 5.29 ms     |
| UCS-4 markup (4 MiB)     | 3.95 ms   | 19.3 ms     |

`unescape` runs against the standard library's
[`html.unescape`](https://docs.python.org/3/library/html.html#html.unescape):

| input                        | turbohtml | html.unescape |
| ---------------------------- | --------- | ------------- |
| tiny plain (64 B)            | 0.02 µs   | 0.03 µs       |
| medium dense refs (4 KiB)    | 8.22 µs   | 69.0 µs       |
| numeric refs (4 KiB)         | 5.83 µs   | 78.7 µs       |
| book HTML, real refs (4 MiB) | 2.44 ms   | 7.87 ms       |
| escaped book HTML (5 MiB)    | 1.90 ms   | 19.5 ms       |
| dense refs (4 MiB)           | 9.89 ms   | 73.0 ms       |
| UCS-2 refs (4 MiB)           | 2.51 ms   | 18.1 ms       |

`escape` gains the most on text that needs little escaping; `unescape` gains the most on entity-heavy input. The gap
narrows on tiny strings, where call overhead dominates.

`tokenize` runs against the standard library's
[`html.parser.HTMLParser`](https://docs.python.org/3/library/html.parser.html) (driven with no-op handlers) and
[html5lib](https://html5lib.readthedocs.io/)'s pure-Python tokenizer, over synthetic cases, html5lib's benchmark corpus
(a slice of the WHATWG spec source plus web-platform-tests pages of varied sizes), and two multi-megabyte
specifications:

| input                       | turbohtml | html.parser | html5lib |
| --------------------------- | --------- | ----------- | -------- |
| typical markup              | 29.3 µs   | 435 µs      | 810 µs   |
| text-heavy prose            | 0.54 µs   | 2.8 µs      | 143 µs   |
| attribute-heavy             | 19.2 µs   | 298 µs      | 807 µs   |
| script-heavy                | 12.1 µs   | 156 µs      | 488 µs   |
| entity-heavy                | 20.4 µs   | 197 µs      | 1.20 ms  |
| wpt page (0.6 kB)           | 1.4 µs    | 17.5 µs     | 47.7 µs  |
| wpt page (4 kB)             | 12.1 µs   | 165 µs      | 422 µs   |
| wpt page (9.6 kB)           | 29.2 µs   | 360 µs      | 1.16 ms  |
| wpt page (92 kB)            | 324 µs    | 4.03 ms     | 8.93 ms  |
| wpt page, CJK (124 kB)      | 584 µs    | 8.45 ms     | 22.6 ms  |
| whatwg spec (235 kB)        | 645 µs    | 7.39 ms     | 19.3 ms  |
| ecmascript spec (3 MB)      | 5.88 ms   | 55.0 ms     | 181 ms   |
| whatwg spec source (7.9 MB) | 35.0 ms   | 389 ms      | 853 ms   |

turbohtml stays ahead even on text-only input, the best case for `html.parser`.

`parse` builds a full WHATWG document tree. It runs against the other Python HTML tree builders:
[lxml](https://lxml.de/), [selectolax](https://github.com/rushter/selectolax),
[BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/bs4/doc/), and
[html5lib](https://html5lib.readthedocs.io/). Each parses the same web-platform-tests pages and specification sources:

| input                       | turbohtml | lxml    | selectolax | BeautifulSoup | html5lib |
| --------------------------- | --------- | ------- | ---------- | ------------- | -------- |
| wpt page (0.6 kB)           | 1.3 µs    | 3.3 µs  | 6.8 µs     | 61.6 µs       | 101 µs   |
| wpt page (4 kB)             | 10.6 µs   | 26.7 µs | 42.1 µs    | 443 µs        | 616 µs   |
| wpt page (9.6 kB)           | 25.4 µs   | 72.6 µs | 107 µs     | 849 µs        | 1.44 ms  |
| wpt page (92 kB)            | 268 µs    | 629 µs  | 920 µs     | 15.5 ms       | 17.0 ms  |
| wpt page, CJK (124 kB)      | 483 µs    | 1.44 ms | 2.30 ms    | 21.5 ms       | 28.0 ms  |
| whatwg spec (235 kB)        | 504 µs    | 1.23 ms | 1.78 ms    | 26.4 ms       | 31.9 ms  |
| ecmascript spec (3 MB)      | 4.42 ms   | 17.5 ms | 15.8 ms    | 183 ms        | 254 ms   |
| whatwg spec source (7.9 MB) | 27.6 ms   | 83.8 ms | 94.8 ms    | 1.66 s        | 1.73 s   |

`parse` runs roughly 2–5× faster than the C parsers lxml and selectolax, and 30–80× faster than the pure-Python
BeautifulSoup and html5lib. Numbers vary with input and hardware.

## Documentation

Full documentation, including tutorials, how-to guides, the API reference, and the design rationale, lives at
[turbohtml.readthedocs.io](https://turbohtml.readthedocs.io).

## License

`turbohtml` is released under the [MIT license](LICENSE).
