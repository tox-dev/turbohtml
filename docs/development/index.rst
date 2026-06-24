#############
 Development
#############

This page covers how we build, test, and maintain turbohtml; the :doc:`performance` page collects the benchmark tables
the rest of the docs link into.

.. toctree::
    :hidden:

    performance

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
the :doc:`performance` page), and ``regen`` (regenerate the entity tables).

*******************************
 Before opening a pull request
*******************************

- Run ``tox r -e fix`` (formatting and linting) and ``tox r -e type``.
- Add tests for the change and keep coverage at 100%. Mark a genuinely unreachable C branch with ``GCOVR_EXCL_BR_LINE``
  and a comment explaining why it cannot run.
- Keep the C output byte for byte identical to the standard library where the two overlap.

****************
 Project layout
****************

.. code-block:: text

    src/turbohtml/
        __init__.py          # public API re-export, typed
        _html.pyi            # type stub for the C extension
        py.typed             # PEP 561 marker
        query.py             # Query, the chainable wrapper
        sanitizer.py         # allowlist HTML sanitizer
        linkify.py           # URL and email autolinker
        migration/           # drop-in shims: bleach, markupsafe, stdlib
        _c/                  # C sources, compiled into one turbohtml._html module
            core/            # module entry point (module.c), shared headers
            tokenizer/       # WHATWG tokenizer and Token/Tokenizer bindings
            dom/             # tree builder and the node object model
            serialize/       # HTML, minify, markdown, text, escape/unescape
            query/           # css/, xpath/, find/ selection engines
            encoding/        # charset prescan and detection
            features/        # sanitize, linkify, links, annotation
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
      - Output modes over a built tree: html5lib ``#document``, minify, markdown, layout text, readability; plus
        escape/unescape and the markupsafe surface.
    - - ``query/``
      - The selection engines, one per subdirectory: ``css/`` (selector matching), ``xpath/`` (XPath 1.0 + EXSLT),
        ``find/`` (``find``/``find_all``).
    - - ``encoding/``
      - Charset prescan and content-based encoding detection.
    - - ``features/``
      - Transforms over a finished tree: ``sanitize``, ``linkify``, ``links``, ``annotation``.
    - - ``data/``
      - Generated static tables (tag and attribute atoms, HTML entities, TLDs). Regenerate with ``tools/generate_*.py``.

Input bytes pass through ``encoding/`` (when detection is requested), then ``tokenizer/`` turns them into tokens, and
``dom/tree.c`` builds the node tree with the WHATWG insertion-mode algorithm. From a built tree you query it
(``query/``), serialize it (``serialize/``), or transform it (``features/``). The Python node types in ``dom/`` wrap the
C tree and expose all of this to users.

.. _architecture-decisions:

************************
 Architecture decisions
************************

**A C extension, built with meson-python.** Escaping and unescaping are hot paths, so the core is C. `meson-python
<https://mesonbuild.com/meson-python/>`_ is the build backend because `hatchling <https://hatch.pypa.io>`_ (used by our
pure-Python projects) does not compile C; meson-python builds C extensions and supports coverage instrumentation.

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
the output, and they stay out of formatting and clang-tidy.

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
<https://docs.pypi.org/trusted-publishers/>`_, so it stores no API token.
