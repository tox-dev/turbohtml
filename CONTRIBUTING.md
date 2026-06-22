# Contributing to turbohtml

Thanks for contributing!

```console
git clone https://github.com/tox-dev/turbohtml
cd turbohtml
git submodule update --init tests/html5lib-tests   # conformance data for the test suite
uvx --with tox-uv tox r -e 3.14   # build, test, and check coverage
```

The [Development docs](https://turbohtml.readthedocs.io/en/latest/development/) cover everything else: the project
layout, the architecture, the pull-request checklist, the coverage gates, and the maintainer tasks (regenerating tables,
releasing).

Everyone interacting with the project is expected to be open, considerate, and respectful of others.
