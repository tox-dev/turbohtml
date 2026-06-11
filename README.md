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

Tokenize markup into a stream of tokens following the WHATWG tokenization algorithm (the state machine passes all 7032
html5lib-tests tokenizer conformance cases):

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

Measured on CPython 3.14 (release build) against the standard library's `html.escape` / `html.unescape`, via
`tox -e bench`:

| operation  | input                      | turbohtml | stdlib  | speedup |
| ---------- | -------------------------- | --------- | ------- | ------- |
| `escape`   | plain prose, no specials   | 0.35 µs   | 2.23 µs | 6.3×    |
| `escape`   | typical HTML markup        | 4.49 µs   | 10.5 µs | 2.3×    |
| `escape`   | special-dense              | 2.99 µs   | 26.5 µs | 8.9×    |
| `escape`   | non-ASCII prose (UCS-2)    | 0.92 µs   | 1.88 µs | 2.0×    |
| `escape`   | astral text (UCS-4)        | 2.58 µs   | 2.65 µs | 1.0×    |
| `unescape` | named references (dense)   | 18.1 µs   | 70.2 µs | 3.9×    |
| `unescape` | numeric references (dense) | 4.16 µs   | 76.8 µs | 18.5×   |
| `unescape` | mixed named + numeric      | 8.03 µs   | 35.2 µs | 4.4×    |
| `unescape` | prose, sparse references   | 3.93 µs   | 3.87 µs | ~1×     |
| `unescape` | non-ASCII with references  | 9.44 µs   | 35.2 µs | 3.7×    |

`escape` gains the most on text that needs little escaping — the SWAR scan skips eight safe bytes at a time — and
`unescape` gains the most on entity-heavy input, especially numeric references, where the standard library pays a Python
function call per match. Where the text is mostly plain, `unescape` ties the standard library, whose regex already
short-circuits and runs in C. Numbers vary with input and hardware; reproduce them with `tox -e bench`.

`tokenize` is compared against the standard library's `html.parser.HTMLParser` (driven with no-op handlers) and
html5lib's pure-Python tokenizer, over synthetic cases and html5lib's benchmark corpus of real documents (a slice of the
WHATWG spec source plus web-platform-tests pages of varied sizes):

| input                  | turbohtml | `html.parser` | speedup | html5lib | speedup |
| ---------------------- | --------- | ------------- | ------- | -------- | ------- |
| typical markup         | 47.3 µs   | 451 µs        | 9.5×    | 850 µs   | 18×     |
| text-heavy prose       | 4.5 µs    | 2.9 µs        | 0.7×    | 150 µs   | 33×     |
| attribute-heavy        | 33.5 µs   | 315 µs        | 9.4×    | 846 µs   | 25×     |
| script-heavy           | 18.0 µs   | 165 µs        | 9.1×    | 513 µs   | 28×     |
| entity-heavy           | 34.0 µs   | 199 µs        | 5.9×    | 1240 µs  | 36×     |
| wpt page (0.6 kB)      | 2.5 µs    | 18.7 µs       | 7.5×    | 50 µs    | 20×     |
| wpt page (9.6 kB)      | 47.7 µs   | 380 µs        | 8.0×    | 1208 µs  | 25×     |
| wpt page (92 kB)       | 552 µs    | 4178 µs       | 7.6×    | 9185 µs  | 17×     |
| wpt page, CJK (124 kB) | 890 µs    | 9067 µs       | 10.2×   | 23293 µs | 26×     |
| whatwg spec (235 kB)   | 1167 µs   | 8010 µs       | 6.9×    | 20481 µs | 18×     |

The state machine is stamped per input storage width (the CPython stringlib trick) and, like html5ever, bulk-scans plain
text runs instead of dispatching per character, so ASCII documents stay one byte per character end to end. The one case
the standard library wins — a document that is almost entirely one text node — is where `HTMLParser` does a single C
regex scan and never really tokenizes.

## Documentation

Full documentation, including tutorials, how-to guides, the API reference, and the design rationale, lives at
[turbohtml.readthedocs.io](https://turbohtml.readthedocs.io).

## License

`turbohtml` is released under the [MIT license](LICENSE).
