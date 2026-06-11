# turbohtml

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
| `escape`   | tiny plain (64 B)            | 0.04 µs   | 0.14 µs | 3.6×    |
| `escape`   | medium markup (4 KiB)        | 2.54 µs   | 8.17 µs | 3.2×    |
| `escape`   | no-op prose (4 MiB)          | 0.12 ms   | 2.80 ms | 23.3×   |
| `escape`   | book text (3 MiB)            | 0.71 ms   | 3.12 ms | 4.4×    |
| `escape`   | book HTML (4 MiB)            | 1.38 ms   | 5.06 ms | 3.7×    |
| `escape`   | spec HTML, dense (4 MiB)     | 5.31 ms   | 13.7 ms | 2.6×    |
| `escape`   | UCS-2 plain (4 MiB)          | 0.74 ms   | 2.67 ms | 3.6×    |
| `escape`   | UCS-2 markup (4 MiB)         | 3.73 ms   | 11.7 ms | 3.1×    |
| `escape`   | UCS-4 plain (4 MiB)          | 1.52 ms   | 6.09 ms | 4.0×    |
| `escape`   | UCS-4 markup (4 MiB)         | 4.64 ms   | 21.4 ms | 4.6×    |
| `unescape` | tiny plain (64 B)            | 0.02 µs   | 0.03 µs | 1.4×    |
| `unescape` | medium dense refs (4 KiB)    | 14.8 µs   | 74.4 µs | 5.0×    |
| `unescape` | numeric refs (4 KiB)         | 5.11 µs   | 83.0 µs | 16.2×   |
| `unescape` | book HTML, real refs (4 MiB) | 2.90 ms   | 9.24 ms | 3.2×    |
| `unescape` | escaped book HTML (5 MiB)    | 6.10 ms   | 22.2 ms | 3.6×    |
| `unescape` | dense refs (4 MiB)           | 17.0 ms   | 80.4 ms | 4.7×    |
| `unescape` | UCS-2 refs (4 MiB)           | 5.55 ms   | 20.7 ms | 3.7×    |

`escape` gains the most on text that needs little escaping — the SIMD scan classifies sixteen bytes at a time and copies
clean stretches wholesale — and `unescape` gains the most on entity-heavy input, where the standard library pays a
Python function call per match. The gap is narrowest on tiny strings, where call overhead dominates, and on
special-dense markup, where both sides spend their time writing replacements. Numbers vary with input and hardware;
reproduce them with `tox -e bench`.

`tokenize` is compared against the standard library's `html.parser.HTMLParser` (driven with no-op handlers) and
html5lib's pure-Python tokenizer, over synthetic cases and html5lib's benchmark corpus of real documents (a slice of the
WHATWG spec source plus web-platform-tests pages of varied sizes):

| input                  | turbohtml | `html.parser` | speedup | html5lib | speedup |
| ---------------------- | --------- | ------------- | ------- | -------- | ------- |
| typical markup         | 31.9 µs   | 438 µs        | 13.7×   | 836 µs   | 26×     |
| text-heavy prose       | 0.87 µs   | 2.9 µs        | 3.3×    | 148 µs   | 171×    |
| attribute-heavy        | 26.1 µs   | 353 µs        | 13.5×   | 960 µs   | 37×     |
| script-heavy           | 12.5 µs   | 173 µs        | 13.8×   | 529 µs   | 42×     |
| entity-heavy           | 33.6 µs   | 219 µs        | 6.5×    | 1283 µs  | 38×     |
| wpt page (0.6 kB)      | 1.7 µs    | 19.2 µs       | 11.0×   | 54 µs    | 31×     |
| wpt page (9.6 kB)      | 37.3 µs   | 428 µs        | 11.5×   | 1402 µs  | 38×     |
| wpt page (92 kB)       | 483 µs    | 4432 µs       | 9.2×    | 9410 µs  | 20×     |
| wpt page, CJK (124 kB) | 685 µs    | 9047 µs       | 13.2×   | 23136 µs | 34×     |
| whatwg spec (235 kB)   | 805 µs    | 7954 µs       | 9.9×    | 20328 µs | 25×     |

The state machine is stamped per input storage width (the CPython stringlib trick) and, like html5ever, bulk-scans plain
text runs instead of dispatching per character, so ASCII documents stay one byte per character end to end. Run scanning
uses the same SWAR technique as `escape`, so even a document that is almost entirely one text node — `HTMLParser`'s best
case, a single C regex scan — comes out ahead.

## Documentation

Full documentation, including tutorials, how-to guides, the API reference, and the design rationale, lives at
[turbohtml.readthedocs.io](https://turbohtml.readthedocs.io).

## License

`turbohtml` is released under the [MIT license](LICENSE).
