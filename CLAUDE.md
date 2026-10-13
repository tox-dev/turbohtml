# turbohtml

Commands come from `tox.toml` and `CONTRIBUTING.md`; run each through `uvx --with tox-uv tox r -e <env>`.

- Setup: `git submodule update --init tests/html5lib-tests` fetches the conformance data the test suite needs.
- Build and test: `-e 3.15` builds the C extension and runs the suite with its coverage gate.
- Lint and format: `-e fix` runs pre-commit over all files.
- Type check: `-e type` runs `ty` over `src`, `tests` and `tools`.
- C warnings: `-e warnings` compiles the extension with `-Werror`.
- Docs: `-e docs` builds the documentation.
