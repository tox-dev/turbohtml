# Contributing to turbohtml

Thanks for contributing! turbohtml is a C-accelerated HTML toolkit, and the project values correctness, performance, and
readable, maintainable code in equal measure.

## Quick start

```console
git clone https://github.com/tox-dev/turbohtml
cd turbohtml
git submodule update --init tests/html5lib-tests   # conformance data for the test suite
uvx --with tox-uv tox r -e 3.14   # build, test, and check coverage
```

`tox r -e 3.14` fails unless both Python and C coverage are 100% (line and branch). See the
[Development docs](https://turbohtml.readthedocs.io/en/latest/development.html) for the project layout, the
architectural decisions, and maintainer tasks (regenerating tables, the coverage gates, adding a feature, releasing).

## Before opening a pull request

- Run `tox r -e fix` (formatting and linting) and `tox r -e type`.
- Add tests for any change and keep coverage at 100%; mark genuinely unreachable C branches with `GCOVR_EXCL_BR_LINE`
  and a comment explaining why.
- Keep the C output byte-for-byte identical to the standard library.

## Code of Conduct

Everyone interacting with the project is expected to be open, considerate, and respectful of others.
