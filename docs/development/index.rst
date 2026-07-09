#############
 Development
#############

turbohtml 1.0 was not written by hand. It was built over about a month of continuous background co-work with Anthropic's
Claude Opus 4.8, with some help from Fable, across close to 300 pull requests and commits and many rounds of iteration.
See :doc:`/how-this-was-built` for the full acknowledgment and the libraries and specifications turbohtml stands on.

This page covers how we build, test, and maintain turbohtml; the :doc:`performance` page collects the benchmark tables
the rest of the docs link into, and :doc:`cpyext` covers what running the C core on PyPy costs the codebase.

.. toctree::
    :hidden:

    performance
    cpyext

***************************
 AI-assisted contributions
***************************

AI-assisted contributions are welcome. Much of turbohtml itself was written with AI tools, and you are free to use them.
You are responsible for every line you submit: review it, understand it, and make sure it meets the project's standards
and passes the tests before opening a pull request. The same bar applies whether a human or a model wrote the code.

****************
 Getting set up
****************

turbohtml uses `tox <https://tox.wiki>`_ with `tox-uv <https://github.com/tox-dev/tox-uv>`_; `uv
<https://docs.astral.sh/uv/>`_ manages the interpreters, so you do not need to install Python versions yourself.

.. code-block:: console

    $ git clone https://github.com/tox-dev/turbohtml
    $ cd turbohtml
    $ git submodule update --init tests/html5lib-tests   # conformance data for the test suite
    $ uvx --with tox-uv tox r -e 3.14   # build, test, and check coverage

The ``tests/html5lib-tests`` submodule holds the conformance suite that one of the tests runs against. Do not initialize
every submodule: the ``tools/bench-data`` submodules reference multi-MiB real documents (pinned upstream commits,
nothing copied into this repository) that ``tox r -e bench`` reads and nothing else; fetch them on demand with ``git
submodule update --init --depth 1 tools/bench-data/whatwg-html tools/bench-data/war-and-peace``.

``tox r -e 3.14`` builds the extension, runs the test suite, and **fails unless both Python and C coverage are 100%**
(line and branch). Other environments: ``type`` (`ty <https://github.com/astral-sh/ty>`_), ``docs`` (Sphinx), ``fix``
(`pre-commit <https://pre-commit.com>`_), ``pkg_meta`` (wheel/sdist metadata), ``bench`` (`pyperf
<https://pyperf.readthedocs.io>`_ comparison against each competitor library, each in its own isolated ``uv`` venv; see
the :doc:`performance` page), ``codspeed`` (the pytest-codspeed hot-path benchmarks behind the CI regression gate,
below), and ``regen`` (regenerate the entity tables).

Every pull request runs the ``codspeed`` benchmarks under `CodSpeed <https://codspeed.io>`_ in the ``👷 benchmark``
workflow. It counts the CPU instructions of one benchmark per operation under Valgrind and comments the per-benchmark
delta against the base branch, so a regression shows up before merge instead of in a later profiling session. The
benchmarks in ``tests/benchmarks/`` are generated from the same ``bench.core.OPERATIONS`` registry the ``pyperf`` suite
times, over the same real corpora (``tools/bench/ci.py`` pairs each operation with a lazy loader for one of them -- the
vendored html5lib-python and War-and-Peace documents, the pinned upstream stylesheet and JS library, or the bench's own
inline case), so every operation the project tracks gets a gate and adding an operation adds a benchmark. The benchmarks
run only under ``--codspeed``: the ``codspeed`` environment passes it, and locally they are opt-in (``tox r -e
codspeed`` or ``pytest tests/benchmarks --codspeed``), so the ordinary test run skips them and stays fast.

*******************************
 Before opening a pull request
*******************************

- Run ``tox r -e fix`` (formatting and linting) and ``tox r -e type``.
- Add tests for the change and keep coverage at 100%. Mark a genuinely unreachable C branch with ``GCOVR_EXCL_BR_LINE``
  and a comment explaining why it cannot run.
- Keep the C output byte for byte identical to the standard library where the two overlap.

********************
 Conformance suites
********************

A conformance suite validates one feature against a competitor's or a standards body's **own** test suite rather than
hand-written cases: libxml2, the Unicode ``unicodetools``, the W3C ``xml-conformance-suite`` and ``qt3tests``,
``parse5``, DOMPurify, and so on. These suites live under ``tests/conformance/`` and vendor each oracle as a pinned git
submodule at ``tests/conformance/<oracle>`` (and/or drive a Node oracle in ``tools/bench/node`` -- jsdom, DOMPurify,
parse5). The convention is deliberate:

- A missing oracle **errors, never skips.** A suite whose submodule (or Node oracle) is absent must fail loudly, so a
  half-checked-out tree can never report a green run that silently validated nothing.
- They run only in the dedicated ``🔬 conformance`` CI job, which checks out every submodule recursively and installs the
  Node oracle deps before running ``tox r -e conformance``. The normal test matrix deselects ``tests/conformance``
  (``--ignore``), so it never tries an oracle suite without its oracle.
- It is a pass/fail correctness gate, **not** a coverage gate. The 100% line/branch coverage gate stays on the matrix;
  the conformance job only asserts the oracle suites pass, so it does not run under coverage.

.. code-block:: console

    $ git submodule update --init tests/conformance/parse5   # fetch one oracle
    $ tox r -e conformance                                    # run the suites against their oracles

The vendored ``html5lib-tests`` conformance data is separate: it lives under ``tests/dom``, ``tests/tokenizer``, and
``tests/encoding`` (not ``tests/conformance``) and runs in the ordinary matrix, so it is unaffected by the above.

*********
 Fuzzing
*********

Every untrusted-input entry point has an AddressSanitizer + UndefinedBehaviorSanitizer harness under ``tools/fuzz/``.
Two mechanisms share one driver (``tools/fuzz/fuzz.py``):

- **Standalone C harnesses** for the surfaces whose core decouples from CPython: the IDNA ``ToASCII`` engine
  (``idna_harness.c``, compiled with ``TH_IDNA_STANDALONE``) and the JS minifier (``js_minify_harness.c``, compiled with
  ``JM_STANDALONE``). Both link against the system allocator with no interpreter, so a coverage-guided ``libFuzzer`` run
  (``-fsanitize=fuzzer``) or an ``AFL++`` target drives them directly.
- **An in-process driver** (``tools/fuzz/_targets.py``) for the surfaces that reach the live tree -- ``parse``,
  ``serialize`` (and the parse-serialize round trip), ``sanitize``, the URL parser, and the HTML and CSS minifiers. It
  runs against an extension built with the sanitizers and calls the public API, so a C fault aborts the interpreter with
  a stack trace and the harness survives internal C refactors.

The ``fuzz-smoke`` environment replays a benign seed corpus (``tools/fuzz/corpus/``) once per target. It is fast and
deterministic and gates every pull request in the ``🔒 fuzz`` workflow, so it seeds with valid inputs, never known
crashers. The ``fuzz`` environment adds a mutation loop and escalating-depth structural probes for a per-target budget;
it is the continuous hunt, run weekly and on demand, not a merge gate.

.. code-block:: console

    $ tox r -e fuzz-smoke            # the per-PR gate: benign corpus, no crash expected
    $ tox r -e fuzz -- --minutes 5   # the deep run: mutation + structural probes per target

Add a target by registering a ``bytes``-taking callable in ``TARGETS`` (in-process) and dropping a representative benign
seed under ``tools/fuzz/corpus/<target>/``; add a standalone harness by mirroring ``idna_harness.c`` for any C unit that
compiles free of the CPython boundary. macOS ships no ``libFuzzer`` runtime with Apple Clang, so the coverage-guided
mode needs an LLVM Clang (``brew install llvm``); the corpus-replay and mutation modes run under Apple Clang.

****************
 Project layout
****************

.. code-block:: text

    src/turbohtml/
        __init__.py          # public API re-export, typed
        _html.pyi            # type stub for the C extension
        py.typed             # PEP 561 marker
        extract/             # readability, dates, feed, links, structured data, url helpers
        query/               # Query wrapper, selector match, xpath
        clean/               # allowlist sanitizer, URL and email autolinker
        _internal/           # helpers shared by more than one subsystem: render, minify, selectors, locations
        migration/           # drop-in shims: bleach, markupsafe, stdlib
        _c/                  # C sources, compiled into one turbohtml._html module
            core/            # module entry point (module.c), shared headers
            tokenizer/       # WHATWG tokenizer and Token/Tokenizer bindings
            dom/             # tree builder and the node object model
            serialize/       # HTML, minify, markdown, text, escape/unescape
            query/           # xpath/, find/ selection engines
            encoding/        # charset prescan and detection
            clean/           # sanitize, linkify
            extract/         # readability, structured data, dates, tables, links, feed, annotation
            css/             # minify/, select/ (selectors + css_to_xpath), cssom/
            js/              # JS minifier: lexer, parser, fold, mangle, printer
            data/            # generated tables (do not edit)
    tools/generate_*.py      # regenerate the data/ tables
    tests/                   # pytest suite, mirroring src/turbohtml/_c/

The C sources under ``_c/`` compile into one ``turbohtml._html`` extension, split by subsystem; the package root
re-exports the public names and the Python modules add the higher-level APIs. Users never import ``_html`` directly, and
``_html.pyi`` is its type stub.

Each ``_c/`` subdirectory owns one subsystem:

.. list-table::
    :header-rows: 1
    :widths: 18 82

    - - Directory
      - Responsibility
    - - ``core/``
      - Module entry point (``module.c``), shared declarations and SWAR helpers (``common.h``), ``ascii.h``.
    - - ``tokenizer/``
      - The WHATWG tokenizer state machine and its ``Token``/``Tokenizer`` bindings; character references.
    - - ``dom/``
      - The tree builder (``tree.c``) and the node object model split by PyType (``node``, ``element``, ``leaf``,
        ``document``, ``formatters``).
    - - ``serialize/``
      - Output modes over a built tree: html5lib ``#document``, minify, markdown, layout text; plus escape/unescape and
        the markupsafe surface.
    - - ``query/``
      - The selection engines, one per subdirectory: ``xpath/`` (XPath 1.0 + EXSLT), ``find/`` (``find``/``find_all``);
        CSS selectors live in ``css/select/``.
    - - ``encoding/``
      - Charset prescan and content-based encoding detection.
    - - ``clean/``
      - Transforms that rewrite a finished tree in place: ``sanitize``, ``linkify``.
    - - ``extract/``
      - Pull structured content out of a finished tree: ``readability``, ``structured_data``, ``dates``, ``tables``,
        ``links``, ``feed``, ``annotation``.
    - - ``css/``
      - The CSS engines, one per subdirectory: ``minify/`` (the CSS minifier behind ``convert``), ``select/`` (the
        selector matcher and the ``css_to_xpath`` translation), ``cssom/`` (the cascade and ``getComputedStyle``).
    - - ``js/``
      - The JS minifier behind ``convert``/``clean.minify_js``: a standalone lex-parse-transform-print engine
        (``lexer``, ``parser``, ``ast``, ``fold``, ``mangle``, ``printer``) decoupled from CPython via ``jstypes.h``.
    - - ``data/``
      - Generated static tables (tag and attribute atoms, HTML entities, TLDs). Regenerate with ``tools/generate_*.py``.

Input bytes pass through ``encoding/`` (when detection is requested), then ``tokenizer/`` turns them into tokens, and
``dom/tree.c`` builds the node tree with the WHATWG insertion-mode algorithm. From a built tree you query it
(``query/``), serialize it (``serialize/``), clean it (``clean/``), or extract from it (``extract/``). The Python node
types in ``dom/`` wrap the C tree and expose all of this to users.

.. _architecture-decisions:

************************
 Architecture decisions
************************

**A C extension, built with meson-python.** Escaping and unescaping are hot paths, so the core is C. `meson-python
<https://mesonbuild.com/meson-python/>`_ is the build backend because `hatchling <https://hatch.pypa.io>`_ (used by our
pure-Python projects) does not compile C; meson-python builds C extensions and supports coverage instrumentation.

**Profile-guided optimization on the Linux wheels.** ``tools/pgo_build.py`` builds the manylinux and musllinux extension
twice: instrumented, then against a profile that ``tools/pgo_train.py`` collects by driving the hot operations over the
real corpora. It reaches the parser hot path no other build flag speeds up. The ``codspeed`` gate builds the same way,
so it measures the win the wheels ship; the macOS and Windows wheels and the coverage, sanitizer, and dev builds stay
un-profiled, so those gates are untouched.

**No stable ABI (abi3).** The fast paths require buffer macros outside the :ref:`Limited API <python:stable>`:
``PyUnicode_KIND``, ``PyUnicode_DATA``, ``PyUnicode_READ``, ``PyUnicode_WRITE`` and ``PyUnicode_New`` (see the
`PyUnicode C API <https://docs.python.org/3/c-api/unicode.html>`_ and :PEP:`393`). The `Limited API
<https://docs.python.org/3/c-api/stable.html>`_ exposes per-code-point calls (``PyUnicode_ReadChar`` /
``PyUnicode_WriteChar``) and nothing more, with no access to the underlying buffer, which would remove the SWAR scan
that justifies the package. We ship one wheel per interpreter and let `cibuildwheel <https://cibuildwheel.pypa.io>`_
build the matrix.

**No pure-Python fallback.** :PEP:`399` scopes its pure-Python fallback requirement to standard-library modules. As a
third-party package distributing per-interpreter wheels, turbohtml ships the compiled implementation and nothing else.

**SIMD / SWAR for escape.** Most strings need no escaping, so ``escape`` classifies one-byte strings sixteen bytes at a
time: on NEON a single low-nibble table lookup plus one comparison matches all five specials at once (each has a unique
low nibble, the PSHUFB trick used by pulldown-cmark), on x86-64 SSE2 compares per special, and on other targets a SWAR
word applies the `bit-twiddling "has-zero" trick <https://graphics.stanford.edu/~seander/bithacks.html#ZeroInWord>`_.
The sizing pass accumulates the growth of all matches without branches, and the writing pass copies clean stretches in
bulk, limiting rewrites to the positions a match bitmask singles out. One SWAR pass over a 64-bit word probes UCS-2 /
UCS-4 strings (see :PEP:`393` for the representations) for all five special characters.

**Free-threading ready.** The module has no mutable state (immutable ``str`` inputs, read-only tables), so it declares
``Py_MOD_GIL_NOT_USED`` and per-interpreter GIL support on interpreters that support them. See the `free-threading
extension guide <https://docs.python.org/3/howto/free-threading-extensions.html>`_.

**Exact standard-library parity.** turbohtml reproduces :func:`python:html.escape` and :func:`python:html.unescape` byte
for byte, including ``&#x27;`` for the single quote and the full HTML5 character-reference rules. The suite fuzzes the C
output against the standard library.

**Generated entity tables.** ``tools/generate_html_entities.py`` produces ``_c/data/html_entities.h``. The named
references come from :data:`python:html.entities.html5` (which mirrors the `WHATWG named character references
<https://html.spec.whatwg.org/multipage/named-characters.html>`_); the numeric-charref correction tables derive from the
`WHATWG specification <https://html.spec.whatwg.org/multipage/parsing.html#numeric-character-reference-end-state>`_, not
private standard-library internals, so the C tables never drift from the source of truth.

**Includes are subsystem-qualified.** With ``-I src/turbohtml/_c``, a file includes ``"tokenizer/statemachine.h"``, not
a bare basename, so the path names the subsystem that owns the header.

**Hot paths inline across the subsystem split.** Helpers shared by several translation units in one subsystem (the node
traversal helpers in ``dom/nodes.h``, the serialize primitives in ``serialize/internal.h``) are ``static inline`` in a
shared header, so each unit inlines its own copy; the serialize and tree-builder modes share buffer and tree internals
the same way.

**Generated tables have one owner.** The ``data/`` headers come from ``tools/generate_*.py``; edit the generator, not
the output, and they stay out of formatting and clang-tidy. The network-sourced tables carry a pin so a rebuild stays
deterministic and auditable, and each pin has a SHA-256 companion so a poisoned or silently rewritten source cannot slip
a bad table past a ``.c`` review, the class the xz-utils backdoor used. ``generate_psl.py`` fetches the Public Suffix
List at the ``PSL_COMMIT`` it names rather than off ``main`` and checks the file against ``PSL_SHA256``;
``generate_tlds.py`` refuses to regenerate unless IANA still serves the ``IANA_VERSION`` it expects and the download
matches ``IANA_SHA256``; ``generate_idna.py`` pins ``UNICODE_VERSION`` and a SHA-256 for each UTS #46 and Unicode
Character Database file it downloads. Bump the version or commit and its checksum together, review the table diff, and
let the header banner record the exact commit or version. The `security policy
<https://github.com/tox-dev/turbohtml/blob/main/.github/SECURITY.md>`_ places this in the wider threat model.

**Coverage gates on two toolchains.** Both the gcc (Linux) and llvm-cov (macOS, Windows) gates require full line and
branch coverage; an exclusion needs a written reason that testing it is impossible.

**Tree mutations take a per-tree critical section.** A mutation locks the shared handle for the tree it touches; the
lock is a no-op on the GIL build, and the free-threading matrix and ThreadSanitizer guard it.

******************
 Maintainer tasks
******************

Regenerate the entity tables (after a CPython update changes :mod:`python:html.entities`):

.. code-block:: console

    $ tox r -e regen

Run the full check matrix on your machine (per-interpreter, 3.10–3.15 plus free-threading):

.. code-block:: console

    $ tox r            # all environments
    $ tox r -e 3.13    # a single interpreter

Two gates enforce coverage: Python via `covdefaults <https://github.com/asottile/covdefaults>`_ (100%), and C via `gcovr
<https://gcovr.com>`_ with ``--fail-under-line 100 --fail-under-branch 100`` on an instrumented `meson coverage build
<https://mesonbuild.com/Unit-tests.html#coverage>`_ (``-Db_coverage=true -Db_ndebug=true``). The only excluded branches
are allocation-failure guards that a test cannot trigger; each carries a `gcovr exclusion marker
<https://gcovr.com/en/stable/guide/exclusion-markers.html>`_ and a comment explaining why.

Adding a C feature:

1. Add the source under the owning subsystem (``src/turbohtml/_c/<subsystem>/``) and declare it in that subsystem's
   header.
2. List the source in ``meson.build`` and wire the binding in ``_c/core/module.c`` (or the owning PyType).
3. Add tests under the mirroring ``tests/`` path and keep coverage at 100%; mark any unreachable branch with
   ``GCOVR_EXCL_BR_LINE`` plus a reason.

***********
 Releasing
***********

The ``🚀 Release`` GitHub Actions workflow cuts a release: it builds the sdist and the full wheel matrix with
`cibuildwheel <https://cibuildwheel.pypa.io>`_ and publishes to PyPI via `trusted publishing
<https://docs.pypi.org/trusted-publishers/>`_, so it stores no API token. The Linux wheels build profile-guided (see the
architecture decision above), so that job checks out the training submodules the ``before-build`` hook reads.
