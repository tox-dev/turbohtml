# turbohtml

[![PyPI](https://img.shields.io/pypi/v/turbohtml)](https://pypi.org/project/turbohtml/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/turbohtml.svg)](https://pypi.org/project/turbohtml/)
[![Downloads](https://static.pepy.tech/badge/turbohtml/month)](https://pepy.tech/project/turbohtml)
[![Documentation status](https://readthedocs.org/projects/turbohtml/badge/?version=latest)](https://turbohtml.readthedocs.io/en/latest/?badge=latest)
[![check](https://github.com/tox-dev/turbohtml/actions/workflows/check.yaml/badge.svg)](https://github.com/tox-dev/turbohtml/actions/workflows/check.yaml)

A fast, fully typed HTML toolkit for Python, powered by a C-accelerated core. `turbohtml` provides spec-correct HTML
escaping and unescaping that match the standard library byte for byte, and a WHATWG-conformant streaming tokenizer — all
several times faster than their pure-Python counterparts and ready for the free-threaded build.

## Install

```console
$ pip install turbohtml
```

Wheels are published per interpreter for CPython 3.10–3.15 (including free-threading), so there is nothing to compile.

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

Tokenize markup into a stream of tokens following the WHATWG tokenization algorithm:

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

## Performance

Measured with [pyperf](https://pyperf.readthedocs.io) on CPython 3.14 (release build, Apple M-series) against the
standard library's `html.escape` / `html.unescape`. The multi-MiB inputs stream well past the CPU caches; the book and
spec cases are real documents (Project Gutenberg's *War and Peace*, the WHATWG HTML spec source) pulled in as git
submodules. Reproduce with `tox -e bench`:

| operation  | input                        | turbohtml | stdlib  | speedup |
| ---------- | ---------------------------- | --------- | ------- | ------- |
| `escape`   | tiny plain (64 B)            | 0.04 µs   | 0.11 µs | 2.9×    |
| `escape`   | medium markup (4 KiB)        | 2.38 µs   | 8.09 µs | 3.4×    |
| `escape`   | no-op prose (4 MiB)          | 0.12 ms   | 2.66 ms | 22.2×   |
| `escape`   | book text (3 MiB)            | 0.72 ms   | 2.80 ms | 3.9×    |
| `escape`   | book HTML (4 MiB)            | 1.35 ms   | 4.88 ms | 3.6×    |
| `escape`   | spec HTML, dense (4 MiB)     | 5.27 ms   | 13.3 ms | 2.5×    |
| `escape`   | UCS-2 plain (4 MiB)          | 0.74 ms   | 2.60 ms | 3.5×    |
| `escape`   | UCS-2 markup (4 MiB)         | 3.44 ms   | 11.5 ms | 3.3×    |
| `escape`   | UCS-4 plain (4 MiB)          | 0.97 ms   | 5.58 ms | 5.8×    |
| `escape`   | UCS-4 markup (4 MiB)         | 4.08 ms   | 20.3 ms | 5.0×    |
| `unescape` | tiny plain (64 B)            | 0.02 µs   | 0.03 µs | 1.3×    |
| `unescape` | medium dense refs (4 KiB)    | 8.57 µs   | 72.5 µs | 8.5×    |
| `unescape` | numeric refs (4 KiB)         | 5.24 µs   | 81.1 µs | 15.5×   |
| `unescape` | book HTML, real refs (4 MiB) | 2.80 ms   | 8.96 ms | 3.2×    |
| `unescape` | escaped book HTML (5 MiB)    | 2.10 ms   | 21.2 ms | 10.1×   |
| `unescape` | dense refs (4 MiB)           | 10.4 ms   | 78.5 ms | 7.6×    |
| `unescape` | UCS-2 refs (4 MiB)           | 2.78 ms   | 19.4 ms | 7.0×    |

`escape` gains the most on text that needs little escaping — the SIMD scan classifies sixteen bytes at a time and copies
clean stretches wholesale — and `unescape` gains the most on entity-heavy input, where the standard library pays a
Python function call per match. The gap is narrowest on tiny strings, where call overhead dominates, and on
special-dense markup, where both sides spend their time writing replacements. Numbers vary with input and hardware;
reproduce them with `tox -e bench`.

`tokenize` is compared against the standard library's `html.parser.HTMLParser` (driven with no-op handlers) and
html5lib's pure-Python tokenizer, over synthetic cases, html5lib's benchmark corpus of real documents (a slice of the
WHATWG spec source plus web-platform-tests pages of varied sizes), and two multi-megabyte specifications:

| input                       | turbohtml | `html.parser` | speedup | html5lib | speedup |
| --------------------------- | --------- | ------------- | ------- | -------- | ------- |
| typical markup              | 30.3 µs   | 449 µs        | 14.8×   | 840 µs   | 28×     |
| text-heavy prose            | 0.55 µs   | 2.9 µs        | 5.3×    | 149 µs   | 273×    |
| attribute-heavy             | 24.7 µs   | 330 µs        | 13.3×   | 837 µs   | 34×     |
| script-heavy                | 13.0 µs   | 162 µs        | 12.5×   | 526 µs   | 41×     |
| entity-heavy                | 22.3 µs   | 205 µs        | 9.2×    | 1246 µs  | 56×     |
| wpt page (0.6 kB)           | 1.6 µs    | 18.2 µs       | 11.4×   | 49 µs    | 31×     |
| wpt page (4 kB)             | 15.0 µs   | 176 µs        | 11.8×   | 434 µs   | 29×     |
| wpt page (9.6 kB)           | 34.9 µs   | 376 µs        | 10.8×   | 1190 µs  | 34×     |
| wpt page (92 kB)            | 348 µs    | 4250 µs       | 12.2×   | 9311 µs  | 27×     |
| wpt page, CJK (124 kB)      | 626 µs    | 8926 µs       | 14.3×   | 22844 µs | 37×     |
| whatwg spec (235 kB)        | 701 µs    | 7838 µs       | 11.2×   | 20409 µs | 29×     |
| ecmascript spec (3 MB)      | 7.08 ms   | 57.9 ms       | 8.2×    | 192 ms   | 27×     |
| whatwg spec source (7.9 MB) | 37.0 ms   | 399 ms        | 10.8×   | 907 ms   | 25×     |

The state machine is stamped per input storage width (the CPython stringlib trick) and, like html5ever, bulk-scans plain
text runs instead of dispatching per character, so ASCII documents stay one byte per character end to end. Run scanning
uses the same SWAR technique as `escape`, so even a document that is almost entirely one text node — `HTMLParser`'s best
case, a single C regex scan — comes out ahead.

## Documentation

Full documentation, including tutorials, how-to guides, the API reference, and the design rationale, lives at
[turbohtml.readthedocs.io](https://turbohtml.readthedocs.io).

## License

`turbohtml` is released under the [MIT license](LICENSE).
