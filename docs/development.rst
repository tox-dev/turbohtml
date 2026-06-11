#############
 Development
#############

This page onboards contributors and records how turbohtml is built and maintained.

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

The ``tests/html5lib-tests`` submodule holds the conformance suite used by one of the tests. Do not initialize all
submodules indiscriminately: the ``tools/bench-data`` submodules reference multi-MiB real documents (pinned upstream
commits, nothing copied into this repository) used only by ``tox r -e bench``; fetch them on demand with ``git submodule
update --init --depth 1 tools/bench-data/whatwg-html tools/bench-data/war-and-peace``.

``tox r -e 3.14`` builds the extension, runs the test suite, and **fails unless both Python and C coverage are 100%**
(line and branch). Other environments: ``type`` (`ty <https://github.com/astral-sh/ty>`_), ``docs`` (Sphinx), ``fix``
(`pre-commit <https://pre-commit.com>`_), ``pkg_meta`` (wheel/sdist metadata), ``bench`` (`pyperf
<https://pyperf.readthedocs.io>`_ comparison against the standard library), and ``regen`` (regenerate the entity
tables).

****************
 Project layout
****************

.. code-block:: text

    src/turbohtml/
        __init__.py          # public API re-export, typed
        _html.pyi            # type stub for the C extension
        py.typed             # PEP 561 marker
        turbohtml.h          # internal header shared by the C sources
        escape.c             # html.escape implementation (SIMD / SWAR)
        unescape.c           # html.unescape implementation (entity tables)
        _htmlmodule.c        # module definition; wires escape.c + unescape.c
        html_entities.h      # generated tables (do not edit)
    tools/generate_html_entities.py   # regenerates html_entities.h
    tests/                   # pytest suite (escape + unescape)

The three C files compile into a single ``_html`` extension. They are split per feature for readability and share only
the entry-point declarations in ``turbohtml.h``.

.. _architecture-decisions:

************************
 Architecture decisions
************************

**A C extension, built with meson-python.** Escaping and unescaping are hot paths, so the core is C. `meson-python
<https://mesonbuild.com/meson-python/>`_ is the build backend because `hatchling <https://hatch.pypa.io>`_ (used by our
pure-Python projects) does not compile C; meson-python is a first-class C backend with built-in coverage support.

**No stable ABI (abi3).** The fast paths require the non–\ :ref:`Limited API <python:stable>` buffer macros
``PyUnicode_KIND``, ``PyUnicode_DATA``, ``PyUnicode_READ``, ``PyUnicode_WRITE`` and ``PyUnicode_New`` (see the
`PyUnicode C API <https://docs.python.org/3/c-api/unicode.html>`_ and :PEP:`393`). The `Limited API
<https://docs.python.org/3/c-api/stable.html>`_ only exposes per-code-point calls (``PyUnicode_ReadChar`` /
``PyUnicode_WriteChar``) with no access to the underlying buffer, which would remove the SWAR scan that justifies the
package. We therefore ship one wheel per interpreter and let `cibuildwheel <https://cibuildwheel.pypa.io>`_ build the
matrix.

**No pure-Python fallback.** :PEP:`399` requires a pure-Python fallback only for standard-library modules. As a
third-party package distributing per-interpreter wheels, turbohtml ships only the compiled implementation.

**SIMD / SWAR for escape.** ``escape`` confirms most strings need no escaping, so it classifies one-byte strings sixteen
bytes at a time: on NEON a single low-nibble table lookup plus one comparison matches all five specials at once (each
has a unique low nibble — the PSHUFB trick used by pulldown-cmark), on x86-64 SSE2 compares per special, and elsewhere a
SWAR word applies the `bit-twiddling "has-zero" trick
<https://graphics.stanford.edu/~seander/bithacks.html#ZeroInWord>`_. The sizing pass accumulates the growth of all
matches branchlessly, and the writing pass copies clean stretches wholesale, rewriting only the positions a match
bitmask singles out. UCS-2 / UCS-4 strings (see :PEP:`393` for the representations) are probed for all five special
characters in one SWAR pass over a 64-bit word.

**Free-threading ready.** The module has no mutable state (immutable ``str`` inputs, read-only tables), so it declares
``Py_MOD_GIL_NOT_USED`` and per-interpreter GIL support on interpreters that support them. See the `free-threading
extension guide <https://docs.python.org/3/howto/free-threading-extensions.html>`_.

**Exact standard-library parity.** turbohtml reproduces :func:`python:html.escape` and :func:`python:html.unescape` byte
for byte, including ``&#x27;`` for the single quote and the full HTML5 character-reference rules. The suite fuzzes the C
output against the standard library.

**Generated entity tables.** ``html_entities.h`` is produced by ``tools/generate_html_entities.py``. The named
references come from :data:`python:html.entities.html5` (which mirrors the `WHATWG named character references
<https://html.spec.whatwg.org/multipage/named-characters.html>`_); the numeric-charref correction tables are derived
directly from the `WHATWG specification
<https://html.spec.whatwg.org/multipage/parsing.html#numeric-character-reference-end-state>`_ rather than any private
standard-library internals, so the C tables never drift from the source of truth.

******************
 Maintainer tasks
******************

Regenerate the entity tables (after a CPython update changes :mod:`python:html.entities`):

.. code-block:: console

    $ tox r -e regen

Run the full check matrix locally (per-interpreter, 3.10–3.15 plus free-threading):

.. code-block:: console

    $ tox r            # all environments
    $ tox r -e 3.13    # a single interpreter

Coverage is enforced two ways: Python via `covdefaults <https://github.com/asottile/covdefaults>`_ (100%), and C via
`gcovr <https://gcovr.com>`_ with ``--fail-under-line 100 --fail-under-branch 100`` on an instrumented `meson coverage
build <https://mesonbuild.com/Unit-tests.html#coverage>`_ (``-Db_coverage=true -Db_ndebug=true``). The only excluded
branches are allocation-failure guards that a test cannot trigger; each is marked with a `gcovr exclusion marker
<https://gcovr.com/en/stable/guide/exclusion-markers.html>`_ and a comment explaining why.

Adding a C feature:

1. Add ``src/turbohtml/<feature>.c`` and declare its entry point in ``turbohtml.h``.
2. Add the source to ``meson.build`` and wire the method in ``_htmlmodule.c``.
3. Add tests and keep coverage at 100%; mark any genuinely unreachable branch with ``GCOVR_EXCL_BR_LINE`` plus a reason.

***********
 Releasing
***********

A release is cut by the ``🚀 Release`` GitHub Actions workflow: it builds the sdist and the full wheel matrix with
`cibuildwheel <https://cibuildwheel.pypa.io>`_ and publishes to PyPI via `trusted publishing
<https://docs.pypi.org/trusted-publishers/>`_, so no API token is stored.
