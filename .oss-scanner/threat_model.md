# Threat model

## What this project does and where untrusted input enters

turbohtml is an HTML toolkit for Python. A C extension (`src/turbohtml/_c/`, built as `turbohtml._html`) does the
tokenizing, tree building, querying, serializing and sanitizing; the Python files under `src/turbohtml/` are a thin
typed facade. The project's security policy is `.github/SECURITY.md`, and this file follows it.

We assume every byte and string handed to the library is attacker-controlled, except a sanitizer `Policy` and options
the caller writes. Untrusted input enters through:

- parsing: `turbohtml.parse`, `parse_fragment`, `parse_xml`, `IncrementalParser`, `tokenize`/`Tokenizer`, and the
  streaming `rewrite.rewrite`, on `bytes` or `str`. Bytes go through encoding sniffing and decoding (`detect.detect`,
  `detect.detect_all`, the BOM and `<meta charset>` prescan, the legacy encoding tables in `_c/data/`);
- sanitizing and linkifying: `turbohtml.clean.sanitize`, `sanitize_node`, `sanitize_report`, `linkify`, and the
  `migration.bleach` and `migration.markupsafe` shims that route to them. Their output is assumed to be placed in a web
  page;
- serializing a tree built from untrusted input: `serialize`, `encode`, `html`, `inner_html`, `to_markdown`, `to_text`,
  canonical XML (c14n), and the HTML, CSS and JS minifiers (`minify`, `minify_css`, `minify_js`);
- queries built from untrusted text: CSS selectors (`select`, `matches`, `css_to_xpath`), XPath 1.0 with its EXSLT and
  regex functions (`xpath`, `XPath`), and XSLT stylesheets (`transform.Transform`);
- schema validation: `validate.XMLSchema`, `RelaxNG`, and the HTML conformance checker;
- extraction: `article`/`main_content`, `tables`, `structured_data` (JSON-LD, Microdata, RDFa, OpenGraph), `feed`,
  links, dates, phone numbers, and the URL parser, cleaner, IDNA and public-suffix code in `_c/url/`.

## Components that matter most / least

Most:

- the sanitizer, `_c/clean/sanitize.c` with its Python side in `src/turbohtml/clean/_sanitizer.py`, under the default
  `Policy()`. Output that executes script once a browser parses it is the main class of defect we want found: a
  surviving `<script>`, an `on*` handler, a `javascript:` or `data:` URL in an attribute, mutation XSS where the
  serialized output reparses into a different tree, and namespace confusion across SVG and MathML integration points.
  `linkify` must not create the same;
- memory safety of the C extension on untrusted input: the tokenizer and state machine (`_c/tokenizer/`), the tree
  builder and DOM (`_c/dom/`), the serializers (`_c/serialize/`), the XML parser (`_c/tokenizer/xml.c`), the encoding
  decoders, and the URL and IDNA code (`_c/url/`). Out-of-bounds reads and writes, use-after-free (including through DOM
  mutation, `MutationObserver`, `Range` and iterators held across edits), type confusion, and integer overflow in length
  arithmetic;
- tree-construction divergences from the WHATWG algorithm that move an element across a namespace boundary or hide it
  from the sanitizer's allowlist walk;
- XSLT `xsl:import` resolution (`_c/core/resource.c`): with `import_root` set, a stylesheet must not read a file outside
  that directory.

Less:

- the CSS and JS minifiers, CSSOM and computed style, Markdown and text export, and the extractors. Memory-safety faults
  in them are in scope; wrong output that stays inert is a correctness bug;
- the free-threaded build's data races, when reachable from untrusted input on objects the caller does not share.

Out of scope: `tools/`, `tasks/`, `docs/`, `tests/`, and the submodules (html5lib-tests, html5lib-python, the
conformance suites, the benchmark and fuzz corpora). The generators under `tools/generate_*.py` produce committed tables
from pinned, checksummed sources; a bug in a generator that does not change a committed table is out of scope.

## How to exercise it

The repository is at `/src` with its submodules checked out. The C extension is built twice, both as editable installs
that rebuild on the next import after a source edit:

- `/opt/venv` (on `PATH`): `-Dbuildtype=debugoptimized -Db_ndebug=true` plus `-g -fno-omit-frame-pointer`. Same code as
  the shipped wheels. Build directory `/src/build/debug`.
- `/opt/venv-asan`: ASan and UBSan with `-Dfuzzing=true` and `-DTH_OPERATION_LIMIT=100000`, the build of
  `tox -e fuzz-smoke`. Build directory `/src/build/asan`. A script run directly needs the runtime preloaded:
  `LD_PRELOAD=$(clang -print-file-name=libclang_rt.asan-$(uname -m).so) ASAN_OPTIONS=detect_leaks=0 /opt/venv-asan/bin/python repro.py`.

Tests and fuzzers:

- Test suite: `cd /src && python -m pytest tests --ignore=tests/conformance --ignore=tests/test_fuzz_self_test.py`. The
  sanitizer tests are in `tests/clean/`, parser tests in `tests/tokenizer/` and `tests/dom/`, URL tests in `tests/url/`.
- Fuzz driver, `tools/fuzz/fuzz.py`, run with `/opt/venv-asan/bin/python`: `--mode smoke` replays
  `tests/fuzz_regressions/` and the seed corpus in `tools/fuzz/corpus/<target>/` through every harness;
  `--mode deep --minutes 5` adds mutation and structural probes; `--mode oracle --minutes 5` runs the sanitizer
  wrong-output oracles (`tools/fuzz/sanitize_oracles.py`), which compare sanitized output against an independent
  re-parse by html5lib-python and parse5 (set `PYTHONPATH=/src/tools/html5lib-python`); `--mode round-trip` runs the
  serializer and minifier fixpoint oracles. Crashers land in `.fuzz-crashes/`.
- In-process targets, `tools/fuzz/_targets.py`: `parse`, `serialize`, `roundtrip`, `sanitize`, `url`, `idna`,
  `minify_css`, `minify_html`, `phone`.
- libFuzzer harnesses in `/opt/fuzzers/` (`idna_fuzzer`, `phone_fuzzer`, `css_minify_fuzzer`, `js_minify_fuzzer`), built
  with ASan and UBSan from `tools/fuzz/*_harness.c` and `tools/js_minify_harness.c`. Seeds are in
  `tools/fuzz/corpus/idna`, `phone` and `minify_css`, and `tests/js/_corpus`.
- Node with `parse5`, `jsdom` and `isomorphic-dompurify` is in `tools/bench/node/node_modules`; `jsdom` can show that
  sanitized output runs script when parsed as a browser would.

## How you rate severity

- Critical: memory corruption in the C extension that an attacker controls well enough to write memory (an out-of-bounds
  write, use-after-free or double free with a controllable size or contents), reachable by parsing, sanitizing or
  serializing untrusted input through the public API with default options.
- High: a sanitizer or `linkify` bypass under the default `Policy()` whose output runs script in a current browser; any
  other out-of-bounds write, use-after-free or double free reachable from untrusted input; an out-of-bounds read that
  leaks process memory into the returned output; reading a file outside `import_root` through `xsl:import`.
- Medium: a crash or abort of the interpreter (segfault, failed assertion, stack overflow) from untrusted input with no
  demonstrated corruption; an out-of-bounds read that does not reach the output; a small input that hangs the process or
  causes a runaway allocation (super-linear time, expansion blowup).
- Low: super-linear time or memory that only matters for inputs far larger than the attack payload needs to be, for
  example a quadratic path that takes seconds on megabytes of input.

Every report states the turbohtml version and commit, the exact call and options, and the input. A memory-safety finding
must reproduce in `/opt/venv`, the production-equivalent build; use the ASan build to show where it faults. A sanitizer
bypass must show the sanitized output and the browser-parsed tree (jsdom is fine) in which script runs. A test under
`tests/` that fails before the fix is the most useful patch.

## Anything to leave alone

Per `.github/SECURITY.md`:

- a sanitizer bypass that needs a non-default `Policy` the caller wrote to allow the dangerous element, attribute or URL
  scheme;
- a spec divergence with no security impact, such as a mis-nested `<table>` or an attribute serialized in a different
  but safe form;
- a Python exception raised on malformed input that the caller can catch;
- a finding that reproduces only under the ASan or `-Dfuzzing=true` build (for example, hitting `TH_OPERATION_LIMIT` or
  the fuzz-only allocation-failure hook), or only from trusted, local input the caller already controls;
- `xsl:import` reading local files when the caller left `allow_imports=True` without `import_root`; the documentation
  tells callers to disable imports for untrusted stylesheets;
- `escape`/`unescape` matching `html.escape`/`html.unescape` byte for byte, and `Markup` from `migration.markupsafe`
  treating its argument as trusted markup, which is its contract;
- deliberate depth caps such as `TH_MAX_TREE_DEPTH` flattening nesting past 512 levels.
